"""Meeting state: Firestore is the system of record; every change is also pushed to live sockets.

Layout (Firestore):
  meetings/{id}                       title, created_at, tensor (status, hand)
  meetings/{id}/participants/{name}   name, joined_at, connected
  meetings/{id}/transcript/{seg}      speaker, text, ts (final segments only; interim results are live-only)
  meetings/{id}/chat/{msg}            author, kind, text, sources, attachment, ts
  meetings/{id}/documents/{doc}       name, gcs_uri, status, pages, tables, markdown, summary
  meetings/{id}/action_cards/{card}   PRD schema: type, status, payload, grounding_sources, ...
"""
from __future__ import annotations

import copy
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

TRANSCRIPT_WINDOW_SECONDS = 15 * 60
Listener = Callable[[str, dict], Awaitable[None]]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class MemoryBackend:
    """In-process collections (tests and offline development)."""

    def __init__(self):
        self.data: dict[str, dict[str, dict]] = {}

    async def put(self, path: str, doc_id: str, value: dict) -> None:
        self.data.setdefault(path, {})[doc_id] = copy.deepcopy(value)

    async def get(self, path: str, doc_id: str) -> dict | None:
        value = self.data.get(path, {}).get(doc_id)
        return copy.deepcopy(value) if value else None

    async def list(self, path: str) -> list[dict]:
        return [copy.deepcopy(v) for v in self.data.get(path, {}).values()]

    async def delete(self, path: str, doc_id: str) -> None:
        self.data.get(path, {}).pop(doc_id, None)

    async def create(self, path: str, doc_id: str, value: dict) -> bool:
        """Insert only if absent (unique emails, invite codes)."""
        if doc_id in self.data.get(path, {}):
            return False
        await self.put(path, doc_id, value)
        return True

    async def query(self, path: str, field: str, value) -> list[dict]:
        return [copy.deepcopy(v) for v in self.data.get(path, {}).values() if v.get(field) == value]

    async def transition(self, path: str, doc_id: str, expect: set[str], updates: dict) -> dict | None:
        current = self.data.get(path, {}).get(doc_id)
        if not current or current.get("status") not in expect:
            return None
        current.update(copy.deepcopy(updates))
        return copy.deepcopy(current)


class FirestoreBackend:
    def __init__(self, project: str):
        from google.cloud import firestore

        self.db = firestore.AsyncClient(project=project)
        self._firestore = firestore

    def _col(self, path: str):
        parts = path.split("/")
        ref = self.db.collection(parts[0])
        for i in range(1, len(parts), 2):
            ref = ref.document(parts[i]).collection(parts[i + 1])
        return ref

    async def put(self, path: str, doc_id: str, value: dict) -> None:
        await self._col(path).document(doc_id).set(value)

    async def get(self, path: str, doc_id: str) -> dict | None:
        snap = await self._col(path).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    async def list(self, path: str) -> list[dict]:
        return [d.to_dict() async for d in self._col(path).stream()]

    async def delete(self, path: str, doc_id: str) -> None:
        await self._col(path).document(doc_id).delete()

    async def create(self, path: str, doc_id: str, value: dict) -> bool:
        from google.api_core.exceptions import AlreadyExists

        try:
            await self._col(path).document(doc_id).create(value)
            return True
        except AlreadyExists:
            return False

    async def query(self, path: str, field: str, value) -> list[dict]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        return [d.to_dict() async for d in self._col(path).where(filter=FieldFilter(field, "==", value)).stream()]

    async def transition(self, path: str, doc_id: str, expect: set[str], updates: dict) -> dict | None:
        """Atomic status transition (the approval gate relies on this)."""
        ref = self._col(path).document(doc_id)
        transaction = self.db.transaction()

        @self._firestore.async_transactional
        async def run(tx):
            snap = await ref.get(transaction=tx)
            if not snap.exists or snap.to_dict().get("status") not in expect:
                return None
            tx.update(ref, updates)
            return {**snap.to_dict(), **updates}

        return await run(transaction)


class MeetingStore:
    """Typed operations on meeting state, plus fan-out to live listeners."""

    def __init__(self, backend):
        self.backend = backend
        self.listeners: dict[str, set[Listener]] = {}
        self.interim: dict[str, dict[str, dict]] = {}  # meeting -> speaker -> live interim segment

    # ---- live fan-out --------------------------------------------------------------

    def subscribe(self, meeting_id: str, listener: Listener) -> None:
        self.listeners.setdefault(meeting_id, set()).add(listener)

    def unsubscribe(self, meeting_id: str, listener: Listener) -> None:
        self.listeners.get(meeting_id, set()).discard(listener)

    async def emit(self, meeting_id: str, event: str, data: dict) -> None:
        for listener in list(self.listeners.get(meeting_id, ())):
            try:
                await listener(event, data)
            except Exception:
                self.listeners[meeting_id].discard(listener)

    # ---- meeting -------------------------------------------------------------------

    async def ensure_meeting(self, meeting_id: str, title: str | None = None) -> dict:
        meeting = await self.backend.get("meetings", meeting_id)
        if meeting is None:
            meeting = dict(id=meeting_id, title=title or meeting_id, created_at=now_iso(),
                           tensor=dict(status="idle", hand=None))
            await self.backend.put("meetings", meeting_id, meeting)
        return meeting

    async def set_tensor(self, meeting_id: str, **changes) -> dict:
        meeting = await self.ensure_meeting(meeting_id)
        meeting["tensor"] = {**meeting.get("tensor", {}), **changes, "updated_at": now_iso()}
        await self.backend.put("meetings", meeting_id, meeting)
        await self.emit(meeting_id, "tensor", meeting["tensor"])
        return meeting["tensor"]

    async def tensor(self, meeting_id: str) -> dict:
        return (await self.ensure_meeting(meeting_id)).get("tensor", {})

    # ---- participants ---------------------------------------------------------------

    async def join(self, meeting_id: str, name: str) -> None:
        await self.backend.put(f"meetings/{meeting_id}/participants", name, dict(name=name, joined_at=now_iso(), connected=True))
        await self.emit(meeting_id, "participants", dict(items=await self.participants(meeting_id)))

    async def leave(self, meeting_id: str, name: str) -> None:
        p = await self.backend.get(f"meetings/{meeting_id}/participants", name)
        if p:
            p["connected"] = False
            await self.backend.put(f"meetings/{meeting_id}/participants", name, p)
        await self.emit(meeting_id, "participants", dict(items=await self.participants(meeting_id)))

    async def participants(self, meeting_id: str) -> list[dict]:
        return await self.backend.list(f"meetings/{meeting_id}/participants")

    # ---- transcript -------------------------------------------------------------------

    async def interim_text(self, meeting_id: str, speaker: str, text: str) -> None:
        self.interim.setdefault(meeting_id, {})[speaker] = dict(speaker=speaker, text=text, ts=now_iso())
        await self.emit(meeting_id, "interim", dict(speaker=speaker, text=text))

    async def add_transcript(self, meeting_id: str, speaker: str, text: str) -> dict:
        self.interim.get(meeting_id, {}).pop(speaker, None)
        seg = dict(id=new_id("seg"), speaker=speaker, text=text, ts=now_iso(), epoch=time.time())
        await self.backend.put(f"meetings/{meeting_id}/transcript", seg["id"], seg)
        await self.emit(meeting_id, "transcript", seg)
        return seg

    async def transcript(self, meeting_id: str, window_seconds: float = TRANSCRIPT_WINDOW_SECONDS) -> list[dict]:
        """Rolling window (FR-1.1); older segments are deleted, not just hidden."""
        cutoff = time.time() - window_seconds
        segments = sorted(await self.backend.list(f"meetings/{meeting_id}/transcript"), key=lambda s: s["epoch"])
        for old in [s for s in segments if s["epoch"] < cutoff]:
            await self.backend.delete(f"meetings/{meeting_id}/transcript", old["id"])
        return [s for s in segments if s["epoch"] >= cutoff]

    async def clear_transcript(self, meeting_id: str) -> None:
        self.interim.pop(meeting_id, None)
        for seg in await self.backend.list(f"meetings/{meeting_id}/transcript"):
            await self.backend.delete(f"meetings/{meeting_id}/transcript", seg["id"])

    async def end_meeting(self, meeting_id: str, by: str) -> None:
        meeting = await self.ensure_meeting(meeting_id)
        meeting.update(ended_at=now_iso(), ended_by=by)
        await self.backend.put("meetings", meeting_id, meeting)

    async def reopen(self, meeting_id: str) -> None:
        """Joining an ended meeting starts it again (chat and cards are kept; the transcript was deleted)."""
        meeting = await self.ensure_meeting(meeting_id)
        if meeting.get("ended_at"):
            meeting.update(ended_at=None, ended_by=None)
            await self.backend.put("meetings", meeting_id, meeting)

    # ---- chat ---------------------------------------------------------------------------

    async def post_chat(self, meeting_id: str, author: str, text: str, kind: str = "text", **extra) -> dict:
        msg = dict(id=new_id("msg"), author=author, kind=kind, text=text, ts=now_iso(), epoch=time.time(), **extra)
        await self.backend.put(f"meetings/{meeting_id}/chat", msg["id"], msg)
        await self.emit(meeting_id, "chat", msg)
        return msg

    async def update_chat(self, meeting_id: str, message_id: str, text: str, **changes) -> dict | None:
        """Rewrite a message in place (an answer streaming in); listeners get the same id with the new text."""
        msg = await self.backend.get(f"meetings/{meeting_id}/chat", message_id)
        if not msg:
            return None
        msg.update(text=text, **changes)
        await self.backend.put(f"meetings/{meeting_id}/chat", message_id, msg)
        await self.emit(meeting_id, "chat", msg)
        return msg

    async def chat(self, meeting_id: str) -> list[dict]:
        return sorted(await self.backend.list(f"meetings/{meeting_id}/chat"), key=lambda m: m["epoch"])

    # ---- documents ------------------------------------------------------------------------

    async def put_document(self, meeting_id: str, doc: dict) -> None:
        await self.backend.put(f"meetings/{meeting_id}/documents", doc["id"], doc)
        public = {k: v for k, v in doc.items() if k != "markdown"}
        await self.emit(meeting_id, "document", public)

    async def documents(self, meeting_id: str, with_content: bool = False) -> list[dict]:
        docs = sorted(await self.backend.list(f"meetings/{meeting_id}/documents"), key=lambda d: d.get("epoch", 0))
        return docs if with_content else [{k: v for k, v in d.items() if k != "markdown"} for d in docs]

    async def document(self, meeting_id: str, doc_id: str) -> dict | None:
        return await self.backend.get(f"meetings/{meeting_id}/documents", doc_id)

    # ---- action cards ------------------------------------------------------------------------

    async def put_card(self, meeting_id: str, card: dict) -> dict:
        card["updated_at"] = now_iso()
        await self.backend.put(f"meetings/{meeting_id}/action_cards", card["card_id"], card)
        await self.emit(meeting_id, "card", card)
        return card

    async def card(self, meeting_id: str, card_id: str) -> dict | None:
        return await self.backend.get(f"meetings/{meeting_id}/action_cards", card_id)

    async def cards(self, meeting_id: str) -> list[dict]:
        return sorted(await self.backend.list(f"meetings/{meeting_id}/action_cards"), key=lambda c: c.get("created_at", ""))

    async def transition_card(self, meeting_id: str, card_id: str, expect: set[str], **updates) -> dict | None:
        updates["updated_at"] = now_iso()
        card = await self.backend.transition(f"meetings/{meeting_id}/action_cards", card_id, expect, updates)
        if card:
            await self.emit(meeting_id, "card", card)
        return card

    async def snapshot(self, meeting_id: str) -> dict:
        meeting = await self.ensure_meeting(meeting_id)
        return dict(meeting=meeting, participants=await self.participants(meeting_id),
                    transcript=await self.transcript(meeting_id), chat=await self.chat(meeting_id),
                    documents=await self.documents(meeting_id), cards=await self.cards(meeting_id))


def make_store(kind: str, project: str) -> MeetingStore:
    return MeetingStore(MemoryBackend() if kind == "memory" else FirestoreBackend(project))

