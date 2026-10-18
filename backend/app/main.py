"""Tensor API: accounts and companies, the WebSocket meeting room (audio in, live events out), and REST for
uploads, review cards and company knowledge.

Every meeting, file, card and data request is scoped to the signed-in user's company: a meeting id in the URL
maps to `<company_id>__<meeting_id>` in storage, so two companies can both have a "standup" and never meet.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import templates
from .accounts import SESSION_SECONDS, Accounts, AuthError, public_company, public_user
from .actions import Actions
from .config import Settings
from . import exports
from .documents import DocumentIntelligence
from .knowledge import CompanyKnowledge
from .meeting import MeetingRuntime
from .provision import MAX_KB_UPLOAD, Provisioner
from .speech import DEFAULT_VOICE, VOICES, DualSpeech, Voice, company_phrases
from .store import make_store

log = logging.getLogger(__name__)
STATIC = Path(os.getenv("TENSOR_STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))
KNOWLEDGE_DIR = Path(os.getenv("TENSOR_KNOWLEDGE_DIR", Path(__file__).resolve().parents[2] / "knowledge"))
MEETING_ID = re.compile(r"^[a-z0-9-]{3,40}$")
MAX_UPLOAD = 15 * 1024 * 1024
COOKIE = "tensor_session"


class By(BaseModel):
    by: str | None = Field(default=None, max_length=60)  # ignored: the signed-in user is the actor


class TaskEdit(By):
    title: str | None = None
    story_points: int | None = None
    priority: str | None = None
    assignee: str | None = None
    include: bool | None = None


class EmailEdit(By):
    subject: str | None = None
    body: str | None = None
    add_cc: list[str] | None = None
    remove_cc: list[str] | None = None


class Chat(By):
    text: str = Field(min_length=1, max_length=4000)


class Register(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    company_name: str | None = Field(default=None, max_length=80)
    industry: str | None = Field(default=None, max_length=30)
    invite: str | None = Field(default=None, max_length=40)


class VoiceChoice(BaseModel):
    voice: str = Field(min_length=1, max_length=40)


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


def client_ip(request) -> str:
    """Cloud Run appends the caller's address last to X-Forwarded-For; anything before it is client-supplied."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[-1].strip() if forwarded else (request.client.host if request.client else "unknown")


def same_origin(request) -> bool:
    origin = request.headers.get("origin")
    return not origin or urlparse(origin).netloc == request.headers.get("host")


def create_app(settings: Settings | None = None, store=None, brain=None, knowledge=None, docs=None, actions=None,
               voice=None, speech_factory=None, accounts=None, provisioner=None) -> FastAPI:
    settings = settings or Settings()
    store = store or make_store(settings.store, settings.project)
    knowledge = knowledge or CompanyKnowledge(settings)
    docs = docs or DocumentIntelligence(settings, store)
    actions = actions or Actions(settings, store)
    accounts = accounts or Accounts(settings, store.backend)
    provisioner = provisioner or Provisioner(settings, accounts, knowledge)
    if brain is None and settings.ai_enabled:
        from .agents import TensorBrain

        brain = TensorBrain(settings, store, knowledge, docs, actions)
    if voice is None and settings.ai_enabled:
        voice = Voice(settings)
    runtimes: dict[str, MeetingRuntime] = {}
    sockets: dict[str, dict[str, object]] = {}  # meeting key -> participant name -> push (for WebRTC signalling)
    connections: dict[str, set[WebSocket]] = {}  # meeting key -> open sockets (closed when an admin ends the meeting)
    previews: dict[str, bytes] = {}  # voice id -> sample MP3

    def meeting_key(company: dict, meeting_id: str) -> str:
        if not MEETING_ID.match(meeting_id):
            raise HTTPException(400, "Meeting id: 3-40 lowercase letters, digits or dashes")
        return f"{company['id']}__{meeting_id}"

    def runtime(key: str) -> MeetingRuntime:
        if key not in runtimes:
            loop = asyncio.get_running_loop()
            cid = key.split("__", 1)[0]
            phrases: list[str] = []  # filled from the company record below; read when a participant's mic starts
            factory = speech_factory or ((lambda cb: DualSpeech(settings, loop, cb, phrases=phrases or None)) if settings.ai_enabled else None)

            async def load_phrases():
                phrases[:] = company_phrases(await accounts.company(cid))
            asyncio.create_task(load_phrases())
            runtimes[key] = MeetingRuntime(key, store, brain, actions, voice, factory, company=lambda: accounts.company(cid))
            if brain and hasattr(brain, "warm"):  # someone opened a meeting: connect to Gemini before the first question
                asyncio.create_task(brain.warm())
            if voice and hasattr(voice, "warm"):  # ...and get the voice ready before the first answer
                async def warm():
                    company = await accounts.company(cid)
                    await asyncio.to_thread(voice.warm, (company or {}).get("voice"))
                asyncio.create_task(warm())
        return runtimes[key]

    async def member(request: Request) -> tuple[dict, dict]:
        user = await accounts.session(request.cookies.get(COOKIE))
        if not user:
            raise HTTPException(401, "Please sign in.")
        company = await accounts.company(user["company_id"])
        if not company:
            raise HTTPException(403, "Your company no longer exists.")
        return user, company

    async def admin(ctx: tuple[dict, dict] = Depends(member)) -> tuple[dict, dict]:
        if ctx[0].get("role") != "admin":
            raise HTTPException(403, "Only your company's admin can do that.")
        return ctx

    def session_response(user: dict, company: dict, token: str) -> JSONResponse:
        response = JSONResponse(dict(user=public_user(user), company=public_company(company, user)))
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
        return response

    app = FastAPI(title="Tensor")
    app.state.store, app.state.runtimes, app.state.actions, app.state.accounts = store, runtimes, actions, accounts
    app.state.provisioner = provisioner
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def csrf(request: Request, call_next):
        """Session cookies are SameSite=Lax; state-changing calls must also come from this site."""
        if request.method in ("POST", "PATCH", "PUT", "DELETE") and request.url.path.startswith("/api/") and not same_origin(request):
            return JSONResponse({"error": "Cross-site request refused"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(AuthError)
    async def auth_failed(_, exc: AuthError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.get("/api/health")
    async def health():
        return dict(status="ok", ai=settings.ai_enabled, jira="live" if settings.jira_live else "sandbox",
                    gmail="live" if settings.gmail_live else "sandbox", models=dict(fast=settings.fast_model, planner=settings.planner_model),
                    stt=dict(captions=f"{settings.stt_model} ({settings.stt_language})",
                             transcript=f"{settings.stt_accurate_model} ({settings.stt_accurate_language}, {settings.stt_accurate_location})"))

    # ---- accounts ----------------------------------------------------------------------------------

    @app.get("/api/industries")
    async def industries():
        return [dict(id=k, label=v["label"]) for k, v in templates.INDUSTRIES.items()]

    @app.get("/api/invites/{code}")
    async def invite(code: str):
        ref = await store.backend.get("invites", code) if re.fullmatch(r"[A-Za-z0-9_-]{6,40}", code) else None
        company = ref and await accounts.company(ref["company_id"])
        if not company:
            raise HTTPException(404, "This invite link is no longer valid.")
        return dict(company_name=company["name"], industry_label=company.get("industry_label"))

    @app.post("/api/auth/register")
    async def register(payload: Register, request: Request):
        user, company, token = await accounts.register(
            name=payload.name, email=payload.email, password=payload.password, client=client_ip(request),
            company_name=payload.company_name, industry=payload.industry, invite=payload.invite)
        if company.get("status") == "PROVISIONING" and company.get("created_by") == user["uid"]:
            provisioner.background(provisioner.provision(company))
        return session_response(user, company, token)

    @app.post("/api/auth/login")
    async def login(payload: Login, request: Request):
        user, token = await accounts.login(payload.email, payload.password, client_ip(request))
        return session_response(user, await accounts.company(user["company_id"]), token)

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        await accounts.logout(request.cookies.get(COOKIE))
        response = JSONResponse(dict(ok=True))
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/api/auth/me")
    async def me(ctx=Depends(member)):
        user, company = ctx
        company = await accounts.company(company["id"], fresh=company.get("status") == "PROVISIONING")
        return dict(user=public_user(user), company=public_company(company, user))

    # ---- company -----------------------------------------------------------------------------------

    @app.get("/api/company/members")
    async def members(ctx=Depends(member)):
        return await accounts.members(ctx[1]["id"])

    @app.get("/api/directory")
    async def people(ctx=Depends(member)):
        return [dict(name=p["name"], role=p.get("role", ""), email=p["email"]) for p in ctx[1].get("team", [])]

    @app.get("/api/voices")
    async def voices(ctx=Depends(member)):
        current = ctx[1].get("voice") or DEFAULT_VOICE
        return [dict(id=k, label=v["label"], engine=v["engine"], selected=k == current) for k, v in VOICES.items()]

    @app.get("/api/voices/{voice_id}/preview")
    async def voice_preview(voice_id: str, ctx=Depends(member)):
        if voice_id not in VOICES or not voice:
            raise HTTPException(404, "Unknown voice")
        if voice_id not in previews:
            sample = (f"Hi, I'm Tensor. I found three conflicts in the architecture spec, "
                      f"and I've put the details in the chat for {ctx[1]['name']}.")
            previews[voice_id] = await asyncio.to_thread(voice.synthesize, sample, voice_id)
        return Response(previews[voice_id], media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/company/voice")
    async def choose_voice(payload: VoiceChoice, ctx=Depends(admin)):
        if payload.voice not in VOICES:
            raise HTTPException(422, "Unknown voice")
        await accounts.update_company(ctx[1]["id"], voice=payload.voice)
        return dict(voice=payload.voice)

    @app.post("/api/company/invite")
    async def rotate_invite(ctx=Depends(admin)):
        return dict(invite_code=await accounts.rotate_invite(ctx[1]["id"]))

    @app.post("/api/company/retry")
    async def retry_setup(ctx=Depends(admin)):
        company = ctx[1]
        if company.get("status") != "FAILED":
            raise HTTPException(409, "Company setup isn't in a failed state.")
        company = await accounts.update_company(company["id"], status="PROVISIONING", error=None)
        provisioner.background(provisioner.provision(company))
        return public_company(company, ctx[0])

    @app.get("/api/company/data")
    async def company_data(ctx=Depends(member)):
        company = ctx[1]
        if company.get("status") == "PROVISIONING":
            return dict(dataset=company["dataset"], tables=[], status="PROVISIONING")
        try:
            return {**await asyncio.to_thread(knowledge.describe, company["dataset"]), "status": company.get("status")}
        except Exception as exc:
            log.exception("describe failed")
            raise HTTPException(503, f"Company data unavailable: {type(exc).__name__}")

    @app.get("/api/company/knowledge")
    async def kb_list(ctx=Depends(member)):
        return [{k: v for k, v in d.items() if k != "gcs_uri"} for d in await provisioner.documents(ctx[1]["id"])]

    @app.post("/api/company/knowledge")
    async def kb_upload(ctx=Depends(admin), file: UploadFile = File(...)):
        content = await file.read(MAX_KB_UPLOAD + 1)
        if len(content) > MAX_KB_UPLOAD:
            raise HTTPException(413, "Files must be at most 10 MB")
        doc = await provisioner.add_document(ctx[1]["id"], file.filename or "document", content, ctx[0]["name"])
        return {k: v for k, v in doc.items() if k != "gcs_uri"}

    @app.delete("/api/company/knowledge/{doc_id}")
    async def kb_delete(doc_id: str, ctx=Depends(admin)):
        await provisioner.delete_document(ctx[1]["id"], doc_id)
        return dict(ok=True)

    @app.get("/api/company/knowledge/{doc_id}/file")
    async def kb_file(doc_id: str, ctx=Depends(member)):
        """Citation links. Uploaded HTML is served sandboxed (no scripts, no same-origin access)."""
        doc = await provisioner.document(ctx[1]["id"], doc_id)
        if not doc:
            raise HTTPException(404, "Not found")
        content = await asyncio.to_thread(knowledge.download, doc["gcs_uri"])
        disposition = "inline" if doc["mime"] in ("application/pdf", "text/html", "text/plain") else "attachment"
        safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", doc["name"])
        return Response(content, media_type=doc["mime"], headers={
            "Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'{disposition}; filename="{safe_name}"', "Cache-Control": "private, max-age=300"})

    # ---- meetings ----------------------------------------------------------------------------------

    @app.get("/api/meetings/{meeting_id}")
    async def snapshot(meeting_id: str, ctx=Depends(member)):
        key = meeting_key(ctx[1], meeting_id)
        runtime(key)
        return await store.snapshot(key)

    @app.get("/api/meetings/{meeting_id}/timings")
    async def timings(meeting_id: str, ctx=Depends(member)):
        return runtime(meeting_key(ctx[1], meeting_id)).timings

    @app.websocket("/ws/{meeting_id}")
    async def room(ws: WebSocket, meeting_id: str):
        user = await accounts.session(ws.cookies.get(COOKIE))
        company = user and await accounts.company(user["company_id"])
        if not same_origin(ws) or not MEETING_ID.match(meeting_id):
            await ws.close(code=1008)
            return
        await ws.accept()
        if not user or not company:
            await ws.close(code=4401)  # the app sends the user to sign in (a pre-accept close would arrive as 1006)
            return
        key, name = meeting_key(company, meeting_id), user["name"]
        rt = runtime(key)
        send_lock = asyncio.Lock()

        async def push(event: str, data: dict):
            async with send_lock:
                await ws.send_text(json.dumps(dict(event=event, data=data), default=str))

        store.subscribe(key, push)
        connections.setdefault(key, set()).add(ws)
        await store.reopen(key)
        room_sockets = sockets.setdefault(key, {})
        room_sockets[name] = push  # a second tab of the same person takes over
        await store.join(key, name)
        await push("snapshot", await store.snapshot(key))
        try:
            while True:
                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    rt.audio(name, message["bytes"])
                elif message.get("text"):
                    if len(message["text"]) > 64_000:
                        continue
                    data = json.loads(message["text"])
                    kind = data.get("type")
                    if kind == "rtc":  # WebRTC signalling (offer/answer/ICE) to one participant; voice flows peer to peer
                        target = room_sockets.get(str(data.get("to", "")))
                        if target and isinstance(data.get("data"), dict):
                            try:
                                await target("rtc", {"from": name, "data": data["data"]})
                            except Exception:
                                pass
                    elif kind == "mic":
                        await store.emit(key, "presence", dict(name=name, mic=bool(data.get("on"))))
                    elif kind == "vad":
                        await store.emit(key, "presence", dict(name=name, speaking=bool(data.get("speaking"))))
                        await rt.vad(name, bool(data.get("speaking")))
                    elif kind == "wake_hint":  # fast browser-side keyword hint: UI feedback only, never a command
                        await rt.wake_hint(name)
                    elif kind == "chat" and data.get("text"):
                        asyncio.create_task(rt.on_chat(name, str(data["text"])[:4000]))
                    elif kind == "ping":
                        await push("pong", {})
        except WebSocketDisconnect:
            pass
        finally:
            store.unsubscribe(key, push)
            connections.get(key, set()).discard(ws)
            if room_sockets.get(name) is push:
                room_sockets.pop(name)
            rt.close_participant(name)
            await store.leave(key, name)

    @app.post("/api/meetings/{meeting_id}/chat")
    async def chat(meeting_id: str, payload: Chat, ctx=Depends(member)):
        asyncio.create_task(runtime(meeting_key(ctx[1], meeting_id)).on_chat(ctx[0]["name"], payload.text))
        return dict(ok=True)

    @app.post("/api/meetings/{meeting_id}/documents")
    async def upload(meeting_id: str, file: UploadFile = File(...), ctx=Depends(member)):
        key = meeting_key(ctx[1], meeting_id)
        runtime(key)
        content = await file.read(MAX_UPLOAD + 1)
        if len(content) > MAX_UPLOAD:
            raise HTTPException(413, "PDF must be at most 15 MB")
        if not content.startswith(b"%PDF"):
            raise HTTPException(422, "Only PDF files are supported")
        doc = await docs.ingest(key, file.filename or "document.pdf", content, ctx[0]["name"])
        return {k: v for k, v in doc.items() if k != "markdown"}

    @app.post("/api/meetings/{meeting_id}/cards/{card_id}/approve")
    async def approve(meeting_id: str, card_id: str, payload: By, ctx=Depends(member)):
        key, name = meeting_key(ctx[1], meeting_id), ctx[0]["name"]
        card = await actions.approve(key, card_id, name, f"ui_click:{name}")
        refs = (card.get("result") or {}).get("refs", [])
        await runtime(key).speak(f"Done. Created {', '.join(refs)}." if refs else ("Sent." if card["status"] == "EXECUTED" else None))
        return card

    @app.post("/api/meetings/{meeting_id}/cards/{card_id}/cancel")
    async def cancel(meeting_id: str, card_id: str, payload: By, ctx=Depends(member)):
        return await actions.cancel(meeting_key(ctx[1], meeting_id), card_id, ctx[0]["name"])

    @app.patch("/api/meetings/{meeting_id}/cards/{card_id}/tasks/{temp_id}")
    async def edit_task(meeting_id: str, card_id: str, temp_id: int, payload: TaskEdit, ctx=Depends(member)):
        key, name, team = meeting_key(ctx[1], meeting_id), ctx[0]["name"], ctx[1].get("team")
        card = None
        if payload.include is not None:
            card = await actions.set_task_included(key, card_id, name, temp_id, payload.include)
        fields = dict(title=payload.title, story_points=payload.story_points, priority=payload.priority, assignee=payload.assignee)
        if any(v is not None for v in fields.values()):
            card = await actions.update_jira_task(key, card_id, name, temp_id, people=team, **fields)
        return card or await store.card(key, card_id)

    @app.patch("/api/meetings/{meeting_id}/cards/{card_id}/email")
    async def edit_email(meeting_id: str, card_id: str, payload: EmailEdit, ctx=Depends(member)):
        return await actions.update_email(meeting_key(ctx[1], meeting_id), card_id, ctx[0]["name"], payload.subject, payload.body,
                                          payload.add_cc, payload.remove_cc, people=ctx[1].get("team"))

    MINUTES = "Write the meeting minutes for this meeting."

    @app.post("/api/meetings/{meeting_id}/minutes")
    async def request_minutes(meeting_id: str, payload: By, ctx=Depends(member)):
        """The Minutes button: Tensor writes the minutes like any spoken request (they appear in the Action center)."""
        rt = runtime(meeting_key(ctx[1], meeting_id))
        asyncio.create_task(rt.command(ctx[0]["name"], MINUTES, "button", utterance=MINUTES))
        return dict(ok=True)

    @app.get("/api/meetings/{meeting_id}/minutes/latest")
    async def latest_minutes(meeting_id: str, ctx=Depends(member)):
        """Newest minutes card (the ended-meeting screen polls this while the final minutes are written)."""
        key = meeting_key(ctx[1], meeting_id)
        meeting = await store.backend.get("meetings", key) or {}
        minutes = [c for c in await store.cards(key) if c["type"] == "DOCUMENT" and c["payload"].get("kind") == "minutes"]
        return dict(card=minutes[-1] if minutes else None, writing=bool(meeting.get("minutes_pending")))

    @app.get("/api/meetings/{meeting_id}/exports/{card_id}.{fmt}")
    async def export(meeting_id: str, card_id: str, fmt: str, ctx=Depends(member)):
        card = await store.card(meeting_key(ctx[1], meeting_id), card_id)
        if not card or card["type"] != "DOCUMENT" or fmt not in ("docx", "md"):
            raise HTTPException(404, "Not found")
        title, markdown = card["payload"]["title"], card["payload"]["markdown"]
        if fmt == "md":
            content, media = markdown.encode("utf-8"), "text/markdown; charset=utf-8"
        else:
            content, media = await asyncio.to_thread(exports.to_docx, title, markdown), exports.DOCX_TYPE
        return Response(content, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="{exports.filename(title, fmt)}"', "X-Content-Type-Options": "nosniff"})

    async def final_minutes(key: str, by: str, context: str, company: dict) -> None:
        try:
            await brain.handle(key, by, MINUTES + " The meeting has just ended.", context, company=company)
        except Exception:
            log.exception("final minutes failed")
        finally:
            meeting = await store.backend.get("meetings", key)
            if meeting:
                meeting["minutes_pending"] = False
                await store.backend.put("meetings", key, meeting)

    @app.post("/api/meetings/{meeting_id}/end")
    async def end_meeting(meeting_id: str, payload: By, ctx=Depends(admin)):
        """Admin: end the meeting for everyone and stop everything Tensor is doing in it.
        If anything was said, Tensor first captures the context and writes the minutes in the background."""
        key = meeting_key(ctx[1], meeting_id)
        rt = runtimes.pop(key, None) or MeetingRuntime(key, store, brain, actions)
        context = await rt.context([]) if brain and await store.transcript(key) else None
        await rt.end(ctx[0]["name"])
        if context:
            meeting = await store.ensure_meeting(key)
            meeting["minutes_pending"] = True
            await store.backend.put("meetings", key, meeting)
            asyncio.create_task(final_minutes(key, ctx[0]["name"], context, ctx[1]))
        for socket in list(connections.pop(key, set())):
            try:
                await socket.close(code=4000)  # the app shows "meeting ended" instead of reconnecting
            except Exception:
                pass
        sockets.pop(key, None)
        return dict(ok=True, ended_by=ctx[0]["name"], minutes=bool(context))

    @app.post("/api/meetings/{meeting_id}/hand/speak")
    async def hand_speak(meeting_id: str, payload: By, ctx=Depends(member)):
        await runtime(meeting_key(ctx[1], meeting_id)).speak_raised_hand(ctx[0]["name"])
        return dict(ok=True)

    @app.post("/api/meetings/{meeting_id}/hand/dismiss")
    async def hand_dismiss(meeting_id: str, payload: By, ctx=Depends(member)):
        await runtime(meeting_key(ctx[1], meeting_id)).dismiss_hand(ctx[0]["name"])
        return dict(ok=True)

    @app.get("/kb/{name}")
    async def kb(name: str):
        """Legacy citation links from the original demo knowledge base (fictional documents)."""
        if not re.match(r"^[a-z0-9-]+\.html$", name) or not (KNOWLEDGE_DIR / name).is_file():
            raise HTTPException(404, "Not found")
        return HTMLResponse((KNOWLEDGE_DIR / name).read_text(encoding="utf-8"))

    if (STATIC / "index.html").is_file():
        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str):
            if path.startswith(("api/", "ws/", "kb/")):
                raise HTTPException(404, "Not found")
            target = (STATIC / path).resolve()
            if path and target.is_file() and STATIC.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app() if os.getenv("TENSOR_NO_APP") != "1" else None
