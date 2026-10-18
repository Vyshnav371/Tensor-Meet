"""Meeting memory: what Tensor remembers beyond the 15-minute verbatim transcript, and who contributed what.

  meetings/{id}/memory/state   summary  attributed bullet points of transcript that left the 15-minute window
                               log      Tensor's earlier requests and answers in this meeting (newest last)
                               stats    per speaker: turns, words, first/last spoke (whole meeting)
Everything here is deleted when an admin ends the meeting, together with the transcript.
"""
from __future__ import annotations

import time

from .store import MeetingStore, now_iso

MAX_LOG = 15
MAX_SUMMARY = 80


def _same_person(a: str, b: str) -> bool:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(a and b) and (a == b or a.split()[0] == b.split()[0])


class MeetingMemory:
    def __init__(self, store: MeetingStore, meeting_id: str):
        self.store, self.meeting_id = store, meeting_id
        self.state: dict | None = None

    async def load(self) -> dict:
        if self.state is None:
            self.state = {**dict(summary=[], log=[], stats={}), **(await self.store.memory(self.meeting_id))}
        return self.state

    async def save(self) -> None:
        await self.store.save_memory(self.meeting_id, self.state)

    async def count(self, speaker: str, text: str) -> None:
        state = await self.load()
        s = state["stats"].setdefault(speaker, dict(turns=0, words=0, first=now_iso()))
        s["turns"] += 1
        s["words"] += len(text.split())
        s["last"] = now_iso()
        await self.save()

    async def remember(self, by: str, request: str, answer: str, details: str = "", tools: list[str] | None = None) -> None:
        state = await self.load()
        state["log"].append(dict(at=now_iso(), epoch=time.time(), by=by, request=request[:400], answer=(answer or "")[:400],
                                 details=(details or "")[:700], tools=(tools or [])[:6]))
        del state["log"][:-MAX_LOG]
        await self.save()

    async def add_summary(self, bullets: list[str]) -> None:
        state = await self.load()
        state["summary"] += [b for b in bullets if b]
        del state["summary"][:-MAX_SUMMARY]
        await self.save()

    async def clear(self) -> None:
        self.state = dict(summary=[], log=[], stats={})
        await self.store.clear_memory(self.meeting_id)

    def context_lines(self) -> list[str]:
        state = self.state or {}
        lines = []
        if state.get("summary"):
            lines.append("Summary of the earlier part of this meeting (older than the verbatim transcript):")
            lines += [f"- {b}" for b in state["summary"][-40:]]
        if state.get("stats"):
            total = sum(s["words"] for s in state["stats"].values()) or 1
            lines.append("Participation so far (whole meeting): " + "; ".join(
                f"{name} {s['turns']} turns, {round(100 * s['words'] / total)}% of words" for name, s in state["stats"].items()))
        if state.get("log"):
            lines.append("Earlier requests to Tensor in this meeting (oldest first):")
            for e in state["log"][-8:]:
                detail = f" [details: {e['details'][:300]}]" if e.get("details") else ""
                lines.append(f"- {e['by']} asked: {e['request']} -> Tensor: {e['answer']}{detail}")
        return lines


async def participation(store: MeetingStore, actions, meeting_id: str, company: dict | None) -> dict:
    """Evidence per attendee for contribution/workload questions (no judgement: counts, points, ownership)."""
    from .actions import ticket_list

    memory = await store.memory(meeting_id)
    stats, summary = memory.get("stats", {}), memory.get("summary", [])
    people = await store.participants(meeting_id)
    chat = [m for m in await store.chat(meeting_id) if m.get("kind") != "system" and m["author"] != "Tensor"]
    docs = await store.documents(meeting_id)
    cards = [c for c in await store.cards(meeting_id) if c["status"] != "CANCELLED"]
    transcript = await store.transcript(meeting_id)
    team = (company or {}).get("team", [])
    total_words = sum(s["words"] for s in stats.values()) or 1
    names = list(dict.fromkeys([p["name"] for p in people] + list(stats)))
    out = []
    for name in names:
        s = stats.get(name, {})
        presence = next((p for p in people if p["name"] == name), None)
        role = next((p.get("role") for p in team if _same_person(p["name"], name)), None)
        tickets = [f"{t['key'] or 'draft'} {t['title']} ({t['priority']}, {t['story_points']} pts)"
                   for c in cards if c["type"] == "JIRA_TASK_PLAN" for t in ticket_list(c) if _same_person(t["assignee"], name)]
        out.append(dict(
            name=name, role=role, status=("here" if presence and presence.get("connected") else "left" if presence else "not joined"),
            speaking=dict(turns=s.get("turns", 0), words=s.get("words", 0), share_of_words=f"{round(100 * s.get('words', 0) / total_words)}%",
                          first=s.get("first"), last=s.get("last")),
            chat_messages=sum(1 for m in chat if m["author"] == name),
            files_shared=[d["name"] for d in docs if d.get("uploaded_by") == name],
            recent_points=[seg["text"] for seg in transcript if seg["speaker"] == name][-6:],
            earlier_points=[b for b in summary if name.split()[0].lower() in b.lower()][-8:],
            tickets_assigned_this_meeting=tickets,
            requests_to_tensor=[e["request"] for e in memory.get("log", []) if e["by"] == name][-5:],
            emails_drafted_for_them=[c["payload"]["subject"] for c in cards if c["type"] == "EMAIL_DRAFT" and c.get("triggered_by") == name],
            actions_approved=sum(1 for c in cards if c.get("approved_by") == name),
            documents_requested=[c["payload"]["title"] for c in cards if c["type"] == "DOCUMENT" and c.get("triggered_by") == name]))
    return dict(people=out, note="Speaking counts cover the whole meeting; recent_points are verbatim from the last 15 minutes; "
                                 "earlier_points come from Tensor's running summary. This is evidence of participation, not a "
                                 "measure of anyone's ability.")
