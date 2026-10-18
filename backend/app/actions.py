"""Human-in-the-loop action cards (email drafts, Jira plans) and their gated executors.

Lifecycle: PENDING_REVIEW -> (MODIFIED)* -> APPROVED -> EXECUTING -> EXECUTED | FAILED, or CANCELLED.
Only a human approval (UI click, or an approval phrase detected in code from a participant's speech)
moves a card to APPROVED. Execution is an atomic APPROVED -> EXECUTING transition, so a card can
never run twice. Every execution is appended to the BigQuery audit log.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import uuid
from email.mime.text import MIMEText
from email.utils import formataddr

import httpx

from .config import Settings, directory
from .store import MeetingStore, new_id, now_iso

log = logging.getLogger(__name__)

REVIEWABLE = {"PENDING_REVIEW", "MODIFIED"}
_GREETING = re.compile(r"^\s*(hi|hello|hey|dear|good (morning|afternoon|evening)|greetings)\b", re.I)
_CLOSING = re.compile(r"^\s*(best|kind|warm)?\s*(regards|wishes)|^\s*(thanks|thank you|cheers|sincerely)\b", re.I)


def compose_body(body: str, to: list[dict], requester: str) -> str:
    """House style for every email, whatever the model wrote or rewrote: a greeting, the message, a closing with the
    requester's name, and the Tensor byline."""
    text = re.sub(r"\n?—?\s*Sent via Tensor on behalf of .*$", "", body.strip(), flags=re.S).strip()
    lines = text.splitlines()
    if not lines or not _GREETING.match(lines[0]):
        names = ", ".join(dict.fromkeys(c["name"].split()[0] for c in to)) or "team"
        text = f"Hi {names},\n\n{text}"
    tail = [l for l in text.splitlines() if l.strip()][-3:]
    if not any(_CLOSING.match(l) for l in tail):
        text += f"\n\nBest regards,\n{requester}"
    return f"{text}\n\n—\nSent via Tensor on behalf of {requester}"
PRIORITIES = ["Highest", "High", "Medium", "Low", "Lowest"]
FIBONACCI = [1, 2, 3, 5, 8, 13]


class Actions:
    def __init__(self, settings: Settings, store: MeetingStore, bq_client=None, http_client=None):
        self.settings = settings
        self.store = store
        self._bq = bq_client
        self.http = http_client

    # ---- people -----------------------------------------------------------------------

    def resolve_people(self, names: list[str], people: list[dict] | None = None) -> tuple[list[dict], list[str]]:
        """Match names against the company directory (the demo team when none is given)."""
        people = people or directory(self.settings)
        found, unknown = [], []
        for raw in names:
            key = raw.strip().lower()
            match = next((p for p in people if key in (p.get("key"), p["name"].lower(), p["email"].lower()) or p["name"].lower().startswith(key)), None)
            if match:
                if match not in found:
                    found.append(match)
            elif key:
                unknown.append(raw)
        return found, unknown

    # ---- card creation and edits ----------------------------------------------------------

    async def _new_card(self, meeting_id, card_type, triggered_by, payload, sources, executor) -> dict:
        card = dict(card_id=new_id("act"), type=card_type, status="PENDING_REVIEW", created_at=now_iso(),
                    triggered_by=triggered_by, delivery_mode="UI_CARD_WITH_SHORT_VOICE_PROMPT", payload=payload,
                    grounding_sources=sources or [], executor=executor, history=[dict(at=now_iso(), by="Tensor", change="drafted")])
        return await self.store.put_card(meeting_id, card)

    async def draft_email(self, meeting_id, triggered_by, to, cc, subject, body, sources, people=None) -> dict:
        to_people, unknown_to = self.resolve_people(to, people)
        cc_people, unknown_cc = self.resolve_people(cc, people)
        if not to_people:
            raise ValueError(f"No known recipient among {to}; directory: {[p['name'] for p in people or directory(self.settings)]}")
        payload = dict(to=_contacts(to_people), cc=_contacts(cc_people), subject=subject.strip(),
                       body=compose_body(body, _contacts(to_people), triggered_by), unresolved=unknown_to + unknown_cc,
                       from_name=triggered_by)
        return await self._new_card(meeting_id, "EMAIL_DRAFT", triggered_by, payload, sources, "GMAIL" if self.settings.gmail_live else "SANDBOX_OUTBOX")

    async def draft_jira_plan(self, meeting_id, triggered_by, project_key, epic_title, tasks, sources, people=None) -> dict:
        project_key = re.sub(r"[^A-Z0-9]", "", project_key.upper()) or "SEC"
        proposed = []
        for i, t in enumerate(tasks, start=1):
            proposed.append(_task(i, t, self, people))
        payload = dict(project_key=project_key, epic_title=epic_title.strip(), proposed_tasks=proposed)
        return await self._new_card(meeting_id, "JIRA_TASK_PLAN", triggered_by, payload, sources, "JIRA" if self.settings.jira_live else "SANDBOX_JIRA")

    async def create_document(self, meeting_id, created_by, title, kind, markdown, sources=None) -> dict:
        """A downloadable document (minutes, report). Nothing leaves Tensor, so no approval step: it is READY at once."""
        kind = "minutes" if kind == "minutes" else "document"
        card = dict(card_id=new_id("doc"), type="DOCUMENT", status="READY", created_at=now_iso(), triggered_by=created_by,
                    delivery_mode="UI_CARD", payload=dict(title=title.strip()[:160] or "Untitled", kind=kind, markdown=markdown.strip()),
                    grounding_sources=sources or [], executor="DOCUMENT", history=[dict(at=now_iso(), by="Tensor", change="written")])
        card = await self.store.put_card(meeting_id, card)
        await self.store.post_chat(meeting_id, "Tensor", f"{'Meeting minutes' if kind == 'minutes' else 'Document'} ready: **{card['payload']['title']}**",
                                   kind="export", attachment=dict(card_id=card["card_id"], name=card["payload"]["title"], kind=kind))
        return card

    async def edit_card(self, meeting_id, card_id, by, change, apply) -> dict:
        card = await self.store.card(meeting_id, card_id)
        if not card:
            raise ValueError(f"Unknown card {card_id}")
        if card["status"] not in REVIEWABLE:
            raise ValueError(f"Card is {card['status']}; only cards under review can be edited")
        apply(card["payload"])
        card["status"] = "MODIFIED"
        card["history"].append(dict(at=now_iso(), by=by, change=change))
        return await self.store.put_card(meeting_id, card)

    async def update_email(self, meeting_id, card_id, by, subject=None, body=None, add_cc=None, remove_cc=None, add_to=None, people=None) -> dict:
        def apply(p):
            if subject:
                p["subject"] = subject.strip()
            if body:
                p["body"] = compose_body(body, p["to"], p.get("from_name") or by)
            for names, field in ((add_cc, "cc"), (add_to, "to")):
                if names:
                    found, unknown = self.resolve_people(names, people)
                    known = {c["email"] for c in p[field]}
                    p[field] += [c for c in _contacts(found) if c["email"] not in known]
                    p["unresolved"] = sorted(set(p.get("unresolved", []) + unknown))
            if remove_cc:
                found, _ = self.resolve_people(remove_cc, people)
                drop = {x["email"] for x in found}
                p["cc"] = [c for c in p["cc"] if c["email"] not in drop]
        return await self.edit_card(meeting_id, card_id, by, "edited email draft", apply)

    async def update_jira_task(self, meeting_id, card_id, by, temp_id, people=None, **fields) -> dict:
        def apply(p):
            task = next((t for t in p["proposed_tasks"] if t["temp_id"] == int(temp_id)), None)
            if task is None:
                raise ValueError(f"No task {temp_id} in plan")
            updated = _task(task["temp_id"], {**task, **{k: v for k, v in fields.items() if v is not None}}, self, people)
            task.update(updated)
        changed = ", ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        return await self.edit_card(meeting_id, card_id, by, f"task {temp_id}: {changed}", apply)

    async def set_task_included(self, meeting_id, card_id, by, temp_id, included: bool) -> dict:
        def apply(p):
            for t in p["proposed_tasks"]:
                if t["temp_id"] == int(temp_id):
                    t["include"] = bool(included)
        return await self.edit_card(meeting_id, card_id, by, f"task {temp_id} {'included' if included else 'excluded'}", apply)

    # ---- approval gate and execution ---------------------------------------------------

    async def approve(self, meeting_id, card_id, approved_by, channel) -> dict:
        card = await self.store.transition_card(meeting_id, card_id, REVIEWABLE, status="APPROVED", approved_by=approved_by,
                                                approval_channel=channel, approved_at=now_iso())
        if card is None:
            raise ValueError("Card is not awaiting review (already approved, executed or cancelled)")
        return await self.execute(meeting_id, card_id)

    async def cancel(self, meeting_id, card_id, by) -> dict:
        card = await self.store.transition_card(meeting_id, card_id, REVIEWABLE, status="CANCELLED", cancelled_by=by)
        if card is None:
            raise ValueError("Card is not awaiting review")
        return card

    async def execute(self, meeting_id, card_id) -> dict:
        card = await self.store.transition_card(meeting_id, card_id, {"APPROVED"}, status="EXECUTING")
        if card is None:  # not approved, or already executing/executed: never run twice
            raise ValueError("Only an APPROVED card can execute, and only once")
        try:
            if card["type"] == "EMAIL_DRAFT":
                result = await self._send_email(meeting_id, card)
            elif card["type"] == "JIRA_TASK_PLAN":
                result = await self._create_jira(card)
            else:
                raise ValueError(f"Card type {card['type']} has no executor")
        except Exception as exc:
            log.exception("execution failed")
            return await self.store.transition_card(meeting_id, card_id, {"EXECUTING"}, status="FAILED", error=f"{type(exc).__name__}: {exc}"[:300])
        done = await self.store.transition_card(meeting_id, card_id, {"EXECUTING"}, status="EXECUTED", result=result, executed_at=now_iso())
        await self._audit(meeting_id, done)
        return done

    async def _send_email(self, meeting_id: str, card: dict) -> dict:
        p = card["payload"]
        if not self.settings.gmail_live:
            return dict(executor="SANDBOX_OUTBOX", message_id=f"sandbox-{uuid.uuid4().hex[:12]}", refs=[],
                        note="Sandbox outbox: no email left Tensor. Configure Gmail API credentials to send for real.")
        # Real mail only reaches people who registered in this company: never strangers, never template people.
        company_id = meeting_id.split("__", 1)[0]
        members = {u["email"].lower(): u for u in await self.store.backend.query("users", "company_id", company_id)}
        to = [c for c in p["to"] if c["email"].lower() in members]
        cc = [c for c in p["cc"] if c["email"].lower() in members]
        skipped = [c["name"] for c in p["to"] + p["cc"] if c["email"].lower() not in members]
        if not to:
            raise ValueError("No recipient is a registered member of your company, so nothing was sent. "
                             f"Invite them first (skipped: {', '.join(skipped) or 'none'}).")
        requester = next((u for u in members.values() if u["name"] == card.get("triggered_by")), None)
        token = await asyncio.to_thread(self._gmail_token)
        message = MIMEText(p["body"])
        message["To"] = ", ".join(c["email"] for c in to)
        if cc:
            message["Cc"] = ", ".join(c["email"] for c in cc)
        message["From"] = formataddr((f"Tensor for {card.get('approved_by') or 'your team'}", self.settings.gmail_sender))
        if requester:
            message["Reply-To"] = formataddr((requester["name"], requester["email"]))
        message["Subject"] = p["subject"]
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                                headers={"Authorization": f"Bearer {token}"}, json={"raw": raw})
            r.raise_for_status()
        note = f"Sent from {self.settings.gmail_sender}." + (f" Skipped (not members of your company): {', '.join(skipped)}." if skipped else "")
        return dict(executor="GMAIL", message_id=r.json().get("id"), refs=[], sent_to=[c["email"] for c in to + cc],
                    skipped=skipped, note=note)

    def _gmail_token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials(None, refresh_token=self.settings.gmail_refresh_token, client_id=self.settings.gmail_client_id,
                            client_secret=self.settings.gmail_client_secret, token_uri="https://oauth2.googleapis.com/token",
                            scopes=["https://www.googleapis.com/auth/gmail.send"])
        creds.refresh(Request())
        return creds.token

    async def _create_jira(self, card) -> dict:
        p = card["payload"]
        tasks = [t for t in p["proposed_tasks"] if t.get("include", True)]
        if not self.settings.jira_live:
            # Sandbox keys stay unique across meetings and restarts (counter kept in the store).
            counter = await self.store.backend.get("sandbox", "jira") or {}
            start = counter.get(p["project_key"], 401)
            keys = [f"{p['project_key']}-{start + i}" for i in range(len(tasks))]
            await self.store.backend.put("sandbox", "jira", {**counter, p["project_key"]: start + len(tasks)})
            return dict(executor="SANDBOX_JIRA", refs=keys, links=[],
                        note="Sandbox Jira: no tickets were created in a real Jira site.")
        auth = (self.settings.jira_email, self.settings.jira_api_token)
        base = self.settings.jira_base_url.rstrip("/")
        issues = []
        for t in tasks:
            text = f"{t['description']}\n\nAcceptance criteria:\n" + "\n".join(f"- {a}" for a in t["acceptance_criteria"]) + \
                   f"\n\nStory points: {t['story_points']} · Suggested assignee: {t['assignee']} · Epic: {p['epic_title']} · Drafted by Tensor, approved in meeting."
            issues.append(dict(fields=dict(project=dict(key=p["project_key"]), summary=t["title"], issuetype=dict(name="Task"),
                                           priority=dict(name=t["priority"]), labels=["tensor"],
                                           description=dict(type="doc", version=1, content=[dict(type="paragraph", content=[dict(type="text", text=text)])]))))
        async with httpx.AsyncClient(timeout=30, auth=auth) as http:
            r = await http.post(f"{base}/rest/api/3/issue/bulk", json=dict(issueUpdates=issues))
            r.raise_for_status()
            created = r.json().get("issues", [])
        keys = [i["key"] for i in created]
        return dict(executor="JIRA", refs=keys, links=[f"{base}/browse/{k}" for k in keys], errors=r.json().get("errors", []))

    # ---- audit -----------------------------------------------------------------------------

    @property
    def bq(self):
        if self._bq is None:
            from google.cloud import bigquery

            self._bq = bigquery.Client(project=self.settings.project, location=self.settings.bq_location,
                                       default_job_creation_mode="JOB_CREATION_OPTIONAL")  # short queries: ~0.3 s
        return self._bq

    async def _audit(self, meeting_id, card) -> None:
        row = dict(action_id=new_id("aud"), card_id=card["card_id"], meeting_id=meeting_id, card_type=card["type"], status=card["status"],
                   approved_by=card.get("approved_by"), approval_channel=card.get("approval_channel"),
                   executor=card.get("result", {}).get("executor"), external_refs=card.get("result", {}).get("refs", []),
                   payload_json=json.dumps(card["payload"]), grounding_sources=card.get("grounding_sources", []), executed_at=card.get("executed_at"))
        try:
            errors = await asyncio.to_thread(self.bq.insert_rows_json, f"{self.settings.project}.{self.settings.bq_dataset}.action_audit", [row])
            if errors:
                log.warning("audit insert errors: %s", errors)
        except Exception:
            log.exception("audit insert failed")

    async def recent_results(self, meeting_id) -> list[dict]:
        return [dict(card_id=c["card_id"], type=c["type"], status=c["status"], refs=c.get("result", {}).get("refs", []),
                     links=c.get("result", {}).get("links", []), executor=c.get("result", {}).get("executor"),
                     summary=_card_summary(c), tickets=ticket_list(c), approved_by=c.get("approved_by"))
                for c in await self.store.cards(meeting_id)]

    def company_history(self, company_id: str, limit: int = 20) -> list[dict]:
        """Executed actions from every meeting of one company (BigQuery audit log), newest first."""
        from google.cloud import bigquery

        sql = (f"SELECT meeting_id, card_type, approved_by, approval_channel, executor, external_refs, payload_json, executed_at "
               f"FROM `{self.settings.project}.{self.settings.bq_dataset}.action_audit` "
               f"WHERE STARTS_WITH(meeting_id, @prefix) ORDER BY executed_at DESC LIMIT @n")
        config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("prefix", "STRING", f"{company_id}__"),
                                                           bigquery.ScalarQueryParameter("n", "INT64", max(1, min(int(limit), 50)))],
                                         maximum_bytes_billed=100 * 1024**2)
        out = []
        for row in self.bq.query_and_wait(sql, job_config=config, wait_timeout=30):
            payload = json.loads(row["payload_json"] or "{}")
            card = dict(type=row["card_type"], payload=payload, result=dict(refs=list(row["external_refs"] or [])))
            out.append(dict(meeting=row["meeting_id"].split("__", 1)[-1], type=row["card_type"], executed_at=row["executed_at"].isoformat(),
                            approved_by=row["approved_by"], executor=row["executor"], summary=_card_summary(card), tickets=ticket_list(card)))
        return out


def _contacts(people: list[dict]) -> list[dict]:
    return [dict(name=p["name"], email=p["email"]) for p in people]


def _task(temp_id: int, t: dict, actions: Actions, people: list[dict] | None = None) -> dict:
    points = int(t.get("story_points") or 3)
    points = min(FIBONACCI, key=lambda f: abs(f - points))
    priority = str(t.get("priority") or "Medium").capitalize()
    priority = priority if priority in PRIORITIES else "Medium"
    assignee = t.get("assignee") or "Unassigned"
    if assignee != "Unassigned":
        found, _ = actions.resolve_people([assignee], people)
        assignee = found[0]["name"] if found else f"{assignee} (not in directory)"
    return dict(temp_id=temp_id, title=str(t.get("title", "")).strip()[:200], description=str(t.get("description", "")).strip(),
                acceptance_criteria=[str(a) for a in (t.get("acceptance_criteria") or [])][:6], story_points=points,
                priority=priority, assignee=assignee, include=t.get("include", True))


def ticket_list(c: dict) -> list[dict]:
    """Jira keys matched to the tasks they were created from (keys follow the included tasks, in order)."""
    if c["type"] != "JIRA_TASK_PLAN":
        return []
    tasks = [t for t in c["payload"].get("proposed_tasks", []) if t.get("include", True)]
    refs = (c.get("result") or {}).get("refs", [])
    return [dict(key=refs[i] if i < len(refs) else None, title=t["title"], assignee=t["assignee"], priority=t["priority"],
                 story_points=t["story_points"]) for i, t in enumerate(tasks)]


def _card_summary(c: dict) -> str:
    p = c["payload"]
    if c["type"] == "EMAIL_DRAFT":
        return f"Email to {', '.join(x['name'] for x in p['to'])}: {p['subject']}"
    if c["type"] == "JIRA_TASK_PLAN":
        return f"{len(p['proposed_tasks'])} Jira tasks in {p['project_key']} for '{p['epic_title']}'"
    if c["type"] == "DOCUMENT":
        return f"{'Meeting minutes' if p.get('kind') == 'minutes' else 'Document'} '{p.get('title')}' (downloadable)"
    return c["type"]
