"""Per-meeting runtime: transcripts -> wake / follow-ups / approvals -> brain -> etiquette -> delivery."""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import threading
import time

from . import etiquette, wake
from .actions import REVIEWABLE, Actions, ticket_list
from .store import MeetingStore

log = logging.getLogger(__name__)

HUMAN_ACTIVE_SECONDS = 1.5  # someone spoke this recently -> "humans are talking"
FOLLOWUP_SECONDS = 120  # card edits/approvals without the wake word, after Tensor drafted or edited a card
EMPTY_WAKE_SECONDS = 20  # "Hey Tensor" ... pause ... "what's our SLA?" from the same speaker
WAKE_PROMPT = "I'm here. Go ahead."
AUDIO_CHUNK = 9600  # 0.2 s of 24 kHz PCM per socket message


class MeetingRuntime:
    def __init__(self, meeting_id: str, store: MeetingStore, brain, actions: Actions, voice=None, speech_factory=None, company=None):
        self.meeting_id = meeting_id
        self.company = company  # async () -> company record (name, dataset, team, jira_key) for the brain
        self.store = store
        self.brain = brain
        self.actions = actions
        self.voice = voice
        self.speech_factory = speech_factory
        self.streams: dict[str, object] = {}
        self.voice_activity: dict[str, float] = {}
        self.active_speakers: set[str] = set()
        self.followup_until = 0.0
        self.awaiting_command: tuple[str, float] | None = None  # (speaker, deadline) after an empty wake
        self.wake_timeout: asyncio.TimerHandle | None = None
        self.wake_session = 0  # invalidate a slow acknowledgement once a command starts
        self.speaking_until = 0.0
        self.listening_since = 0.0
        self.lock = asyncio.Lock()  # one brain request at a time per meeting
        self.timings: list[dict] = []
        self.speech_id = 0  # bumping it cancels Tensor's current speech (barge-in, newer speech, meeting end)
        self.current: asyncio.Task | None = None  # the brain request in progress
        self.ended = False

    # ---- audio and voice activity ------------------------------------------------------

    def audio(self, participant: str, chunk: bytes) -> None:
        stream = self.streams.get(participant)
        if stream is None and self.speech_factory:
            stream = self.streams[participant] = self.speech_factory(lambda text, final, p=participant: self.on_speech(p, text, final))
        if stream:
            stream.push(chunk)

    def close_participant(self, participant: str) -> None:
        self.active_speakers.discard(participant)
        self.voice_activity.pop(participant, None)
        stream = self.streams.pop(participant, None)
        if stream:
            stream.close()

    async def vad(self, participant: str, speaking: bool) -> None:
        if speaking:
            self.active_speakers.add(participant)
        else:
            self.active_speakers.discard(participant)
        if speaking:
            self.voice_activity[participant] = time.time()
            if time.time() < self.speaking_until:  # barge-in (FR-1.3): a human talks over Tensor
                self.speaking_until = 0
                self.speech_id += 1  # also stops the rest of a streamed reply
                await self.store.emit(self.meeting_id, "stop_audio", dict(reason="barge_in", by=participant))
                waiting = self.awaiting_command and time.time() < self.awaiting_command[1]
                await self.store.set_tensor(self.meeting_id, status="listening" if waiting else "idle",
                                            note=WAKE_PROMPT if waiting else f"Stopped speaking: {participant} started talking")

    async def wake_hint(self, participant: str) -> None:
        """Instant 'Listening' feedback from the browser's local recognizer; commands still come from the server transcript."""
        if time.time() - self.listening_since > 10 and not self.lock.locked():
            self.listening_since = time.time()
            await self.store.set_tensor(self.meeting_id, status="listening", by=participant, via="browser_hint")

    def humans_speaking(self, exclude: str | None = None) -> bool:
        now = time.time()
        return (any(p != exclude for p in self.active_speakers)
                or any(now - t <= HUMAN_ACTIVE_SECONDS for p, t in self.voice_activity.items() if p != exclude))

    # ---- speech results ------------------------------------------------------------------

    async def on_speech(self, participant: str, text: str, final: bool) -> None:
        if not final:
            await self.store.interim_text(self.meeting_id, participant, text)
            if wake.detect_wake(text) and time.time() - self.listening_since > 10 and not self.lock.locked():
                self.listening_since = time.time()  # dedupe: interim results arrive several times per second
                await self.store.set_tensor(self.meeting_id, status="listening", by=participant)
            return
        await self.store.add_transcript(self.meeting_id, participant, text)
        await self.on_utterance(participant, text, source="voice")

    async def on_chat(self, participant: str, text: str) -> None:
        await self.store.post_chat(self.meeting_id, participant, text)
        command = wake.chat_mention(text)
        if command is not None:
            await self.on_utterance(participant, f"Hey Tensor {command}", source="chat")

    async def on_utterance(self, participant: str, text: str, source: str) -> None:
        if self.awaiting_command and time.time() >= self.awaiting_command[1]:
            await self._expire_wake(self.awaiting_command)
        tensor = await self.store.tensor(self.meeting_id)
        hand = tensor.get("hand") or {}
        if hand.get("raised") and wake.wants_raised_answer(text):
            await self.speak_raised_hand(participant)
            return
        match = wake.detect_wake(text)
        if match is None and self.awaiting_command and self.awaiting_command[0] == participant and time.time() < self.awaiting_command[1]:
            match = wake.WakeMatch(command=text)
        if match is not None:
            if not match.command:
                if self.lock.locked():
                    return  # a repeated name must not overwrite an in-progress request's state
                repeated = self.awaiting_command is not None and self.awaiting_command[0] == participant
                if not repeated:
                    self.wake_session += 1
                if self.wake_timeout:
                    self.wake_timeout.cancel()
                waiting = self.awaiting_command = (participant, time.time() + EMPTY_WAKE_SECONDS)
                self.wake_timeout = asyncio.get_running_loop().call_later(
                    EMPTY_WAKE_SECONDS, lambda: asyncio.create_task(self._expire_wake(waiting)))
                session = self.wake_session
                self.listening_since = time.time()
                await self.store.set_tensor(self.meeting_id, status="listening", by=participant,
                                            awaiting_command=True, note=WAKE_PROMPT, command=None)
                if not repeated:
                    await self.store.post_chat(self.meeting_id, "Tensor", WAKE_PROMPT, kind="system")
                    await self._acknowledge_wake(participant, session)
                return
            self._clear_wake()
            self.listening_since = time.time()
            await self.command(participant, match.command, source, utterance=text)
            return
        if time.time() < self.followup_until and wake.is_card_followup(text) and await self.pending_cards():
            await self.command(participant, text, source, utterance=text, followup=True)

    def _clear_wake(self) -> None:
        self.awaiting_command = None
        self.wake_session += 1
        if self.wake_timeout:
            self.wake_timeout.cancel()
            self.wake_timeout = None

    async def _expire_wake(self, waiting: tuple[str, float]) -> None:
        if self.awaiting_command != waiting:
            return  # a later wake has refreshed the window
        self._clear_wake()
        tensor = await self.store.tensor(self.meeting_id)
        changes = dict(awaiting_command=False)
        if tensor.get("status") == "listening":
            changes.update(status="idle", note=None)
        await self.store.set_tensor(self.meeting_id, **changes)

    async def _acknowledge_wake(self, participant: str, session: int) -> None:
        """A brief acknowledgement, while remaining ready for the caller's next segment."""
        if self.humans_speaking(exclude=participant):
            return  # the visual/chat acknowledgement is enough while another person is talking
        sid = self._next_speech()
        job = await self._prepare(WAKE_PROMPT)
        waiting = self.awaiting_command
        if (session != self.wake_session or not waiting or waiting[0] != participant or sid != self.speech_id
                or time.time() >= waiting[1] or self.lock.locked() or self.active_speakers
                or self.humans_speaking(exclude=participant)):
            _cancel(job)
            return  # don't play a late greeting over the answer or a different participant
        seconds = etiquette.spoken_seconds(WAKE_PROMPT)
        self.speaking_until = time.time() + seconds + 1.5
        await self._emit_speech(WAKE_PROMPT, seconds, sid, job)

    # ---- commands --------------------------------------------------------------------------

    async def pending_cards(self) -> list[dict]:
        return [c for c in await self.store.cards(self.meeting_id) if c["status"] in REVIEWABLE]

    async def command(self, participant: str, command: str, source: str, utterance: str, followup: bool = False) -> None:
        if self.ended:
            return
        self._clear_wake()
        async with self.lock:
            self.current = asyncio.current_task()
            started = time.time()
            pending = await self.pending_cards()
            approval, cancel = wake.is_approval(utterance), wake.is_cancel(utterance)
            # Pure approval / cancel of a pending card needs no model: decided in code from the human's words.
            if pending and (approval or cancel) and not wake.EDIT.search(_strip_approval(utterance)):
                await self.resolve_cards(participant, select_cards(pending, utterance), approve=approval, channel=f"{source}:{participant}")
                return
            await self.store.set_tensor(self.meeting_id, status="thinking", by=participant, command=command, awaiting_command=False)
            live = LiveAnswer(self, participant, source)
            try:
                company = await self.company() if self.company else None
                docs = await self.store.documents(self.meeting_id)
                result = await self.brain.handle(self.meeting_id, participant, command, await self.context(pending), company=company,
                                                 has_documents=bool(docs), pending_types=[c["type"] for c in pending],
                                                 on_partial=live.update)
            except Exception as exc:
                log.exception("brain failed")
                await self.store.set_tensor(self.meeting_id, status="idle")
                await self.store.post_chat(self.meeting_id, "Tensor", f"Sorry, I hit an error: {type(exc).__name__}. Please try again.", kind="system")
                return
            touched = list(dict.fromkeys(result.cards_created + result.cards_updated))
            if touched:
                self.followup_until = time.time() + FOLLOWUP_SECONDS
            if approval and touched:  # "Change the first one to High priority and go ahead"
                cards = [c for c in await self.pending_cards() if c["card_id"] in touched]
                await self.resolve_cards(participant, cards, approve=True, channel=f"{source}:{participant}")
                self.record(started, "edit+approve")
                return
            if await live.finish(result):  # already spoken / hand raised while the details were being written
                self.record(started, f"{live.mode} (live)")
                return
            delivery = etiquette.route(result.spoken, result.details, self.humans_speaking(exclude=None if source == "chat" else participant),
                                       created_card=bool(result.cards_created))
            await self.deliver(delivery, sources=result.tools)
            self.record(started, delivery.mode)

    async def resolve_cards(self, participant: str, cards: list[dict], approve: bool, channel: str) -> None:
        done = []
        for card in cards:
            try:
                if approve:
                    done.append(await self.actions.approve(self.meeting_id, card["card_id"], participant, channel))
                else:
                    done.append(await self.actions.cancel(self.meeting_id, card["card_id"], participant))
            except ValueError as exc:
                await self.store.post_chat(self.meeting_id, "Tensor", f"Couldn't update {card['card_id']}: {exc}", kind="system")
        if not done:
            return
        if approve:
            refs = [r for c in done for r in (c.get("result") or {}).get("refs", [])]
            sandbox = any("SANDBOX" in ((c.get("result") or {}).get("executor") or "") for c in done)
            failed = [c for c in done if c["status"] == "FAILED"]
            if failed:
                text = f"That failed: {failed[0].get('error', 'unknown error')}."
            elif refs:
                text = f"Done. Created {', '.join(refs)}." + (" Sandbox mode." if sandbox else "")
            else:
                text = "Done, it's sent." + (" Sandbox outbox." if sandbox else "")
        else:
            text = "Okay, I've cancelled the draft."
        await self.speak(text)

    async def context(self, pending: list[dict]) -> str:
        transcript = await self.store.transcript(self.meeting_id)
        chat = [m for m in await self.store.chat(self.meeting_id) if m.get("kind") != "system"][-30:]
        docs = await self.store.documents(self.meeting_id)
        people = await self.store.participants(self.meeting_id)
        here = [p["name"] for p in people if p.get("connected")]
        left = [p["name"] for p in people if not p.get("connected")]
        meeting = self.meeting_id.split("__", 1)[-1]
        from datetime import datetime, timezone
        lines = [f"Meeting: {meeting} ({len(here)} people connected, plus Tensor). Today is {datetime.now(timezone.utc):%A %d %B %Y} (UTC).",
                 "In the meeting now: " + (", ".join(here) or "nobody"),
                 "Joined earlier but left: " + (", ".join(left) or "nobody"),
                 "Recent meeting transcript (oldest first, speaker-labelled):"]
        lines += [f"- {s['speaker']}: {s['text']}" for s in transcript[-60:]] or ["- (none)"]
        lines.append("Meeting chat (oldest first):")
        lines += [f"- {m['author']}: {m['text'][:600]}" for m in chat] or ["- (none)"]
        lines.append("Files shared in chat: " + (", ".join(f"{d['name']} ({d['status']})" for d in docs) or "none"))
        if pending:
            lines.append("Draft cards awaiting review (edit them by card_id):")
            for c in pending:
                p = c["payload"]
                if c["type"] == "JIRA_TASK_PLAN":
                    tasks = "; ".join(f"#{t['temp_id']} {t['title']} ({t['priority']}, {t['story_points']} pts, {t['assignee']})" for t in p["proposed_tasks"])
                    lines.append(f"- {c['card_id']} JIRA_TASK_PLAN {p['project_key']}: {tasks}")
                else:
                    lines.append(f"- {c['card_id']} EMAIL_DRAFT to {', '.join(x['name'] for x in p['to'])}: {p['subject']}")
        done = [c for c in await self.store.cards(self.meeting_id) if c["status"] not in REVIEWABLE]
        if done:
            lines.append("Actions already decided in this meeting (Action center), oldest first:")
            for c in done:
                p, who = c["payload"], c.get("approved_by") or c.get("cancelled_by") or "-"
                if c["type"] == "DOCUMENT":
                    lines.append(f"- Document '{p['title']}' ({p['kind']}), written for {c.get('triggered_by')}, downloadable in the Action center")
                    continue
                if c["type"] == "JIRA_TASK_PLAN":
                    sandbox = " (sandbox: not in a real Jira)" if "SANDBOX" in ((c.get("result") or {}).get("executor") or "") else ""
                    tickets = "; ".join(f"{t['key'] or '#' + str(i + 1)} {t['title']} ({t['assignee']}, {t['priority']}, {t['story_points']} pts)"
                                        for i, t in enumerate(ticket_list(c)))
                    lines.append(f"- {c['status']} Jira plan '{p['epic_title']}' in {p['project_key']}{sandbox}, by {who}: {tickets}")
                else:
                    lines.append(f"- {c['status']} email to {', '.join(x['name'] for x in p['to'])} (subject: {p['subject']}), by {who}")
        return "\n".join(lines)

    # ---- delivery ----------------------------------------------------------------------------

    async def deliver(self, d: etiquette.Delivery, sources: list[str]) -> None:
        message = None
        if d.chat:
            message = await self.store.post_chat(self.meeting_id, "Tensor", d.chat, kind="markdown", delivery=d.mode, tools=sources)
        if d.mode == "HAND_RAISE":
            await self.store.set_tensor(self.meeting_id, status="hand_raised",
                                        hand=dict(raised=True, topic=d.hand_topic, speech=d.pending_speech, message_id=message and message["id"]))
            return
        await self.speak(d.speak)

    async def speak_raised_hand(self, by: str) -> None:
        self._clear_wake()
        hand = (await self.store.tensor(self.meeting_id)).get("hand") or {}
        await self.store.set_tensor(self.meeting_id, hand=None, status="idle", awaiting_command=False)
        if hand.get("speech"):
            await self.speak(hand["speech"] + " The details are in the chat.")

    async def dismiss_hand(self, by: str) -> None:
        self._clear_wake()
        await self.store.set_tensor(self.meeting_id, hand=None, status="idle", awaiting_command=False, note=f"Hand lowered by {by}")

    async def speak(self, text: str | None) -> None:
        if not text:
            await self.store.set_tensor(self.meeting_id, status="idle")
            return
        sid = self._next_speech()
        job = await self._prepare(text)
        if sid != self.speech_id or self.ended:
            _cancel(job)
            return  # superseded: someone barged in, or newer speech started
        seconds = etiquette.spoken_seconds(text)
        self.speaking_until = time.time() + seconds + 1.5
        await self.store.set_tensor(self.meeting_id, status="speaking", text=text, hand=None, awaiting_command=False)
        await self._emit_speech(text, seconds, sid, job)
        asyncio.get_running_loop().call_later(seconds + 2, lambda: asyncio.ensure_future(self._finish_speaking()))

    # ---- voice: streamed as it's generated -------------------------------------------------------

    def _next_speech(self) -> int:
        self.speech_id += 1
        return self.speech_id

    async def _voice_id(self) -> str | None:
        company = await self.company() if self.company else None
        return (company or {}).get("voice")

    async def _prepare(self, text: str) -> dict | None:
        """Start generating speech and wait for its first audio (about 1 s), so Tensor only commits once it can talk."""
        if not self.voice:
            return None
        if not hasattr(self.voice, "stream"):
            return await self._whole_mp3(text)
        voice_id = await self._voice_id()
        loop, queue, cancel = asyncio.get_running_loop(), asyncio.Queue(), threading.Event()

        def work():
            try:
                for chunk in self.voice.stream(text, voice_id):
                    if cancel.is_set():
                        return
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)

        threading.Thread(target=work, daemon=True, name="tts").start()
        first = await queue.get()
        if not isinstance(first, bytes):
            cancel.set()
            log.warning("streaming text-to-speech failed (%s); falling back to MP3", first)
            return await self._whole_mp3(text)
        return dict(queue=queue, first=first, cancel=cancel)

    async def _whole_mp3(self, text: str) -> dict | None:
        try:
            synthesize = self.voice.synthesize
            audio = await asyncio.to_thread(synthesize, text, await self._voice_id()) if hasattr(self.voice, "stream") \
                else await asyncio.to_thread(synthesize, text)
            return dict(mp3=base64.b64encode(audio).decode())
        except Exception:
            log.exception("text-to-speech failed")
            return None

    async def _emit_speech(self, text: str, seconds: float, sid: int, job: dict | None) -> None:
        streamed = bool(job and "queue" in job)
        await self.store.emit(self.meeting_id, "speak", dict(text=text, seconds=seconds, id=sid, stream=streamed,
                                                             audio_mp3_base64=(job or {}).get("mp3")))
        if streamed:
            asyncio.create_task(self._pump(job, sid))

    async def _pump(self, job: dict, sid: int) -> None:
        """Forward PCM to every browser in ~0.2 s messages until done or cancelled (barge-in, meeting end)."""
        buf, seq, item = b"", 0, job["first"]
        while isinstance(item, bytes):
            if sid != self.speech_id:
                job["cancel"].set()
                return
            buf += item
            if seq == 0 or len(buf) >= AUDIO_CHUNK:  # send the first audio at once: it's what people wait for
                cut = len(buf) - len(buf) % 2
                await self.store.emit(self.meeting_id, "audio", dict(id=sid, seq=seq, pcm=base64.b64encode(buf[:cut]).decode()))
                buf, seq = buf[cut:], seq + 1
            item = await job["queue"].get()
        if isinstance(item, Exception):
            log.warning("text-to-speech stream ended early: %s", item)
        if sid == self.speech_id:
            cut = len(buf) - len(buf) % 2
            await self.store.emit(self.meeting_id, "audio", dict(id=sid, seq=seq, pcm=base64.b64encode(buf[:cut]).decode(), end=True))

    # ---- ending the meeting ------------------------------------------------------------------------

    async def end(self, by: str) -> None:
        """Stop everything: speech, transcription, any request in progress, open drafts, and the transcript."""
        self.ended = True
        self.speech_id += 1
        self.speaking_until = 0
        self._clear_wake()
        if self.current and not self.current.done() and self.current is not asyncio.current_task():
            self.current.cancel()
        for participant in list(self.streams):
            self.close_participant(participant)
        for card in await self.pending_cards():
            try:
                await self.actions.cancel(self.meeting_id, card["card_id"], f"{by} (meeting ended)")
            except ValueError:
                pass
        await self.store.clear_transcript(self.meeting_id)
        await self.store.set_tensor(self.meeting_id, status="idle", hand=None, awaiting_command=False, note=None, text=None)
        await self.store.end_meeting(self.meeting_id, by)
        await self.store.emit(self.meeting_id, "stop_audio", dict(reason="meeting_ended", by=by))
        await self.store.emit(self.meeting_id, "ended", dict(by=by))

    async def _finish_speaking(self) -> None:
        if time.time() >= self.speaking_until and (await self.store.tensor(self.meeting_id)).get("status") == "speaking":
            await self.store.set_tensor(self.meeting_id, status="idle")

    def record(self, started: float, mode: str) -> None:
        self.timings.append(dict(mode=mode, seconds=round(time.time() - started, 2)))
        del self.timings[:-50]


class LiveAnswer:
    """Deliver an answer while it is still being written: speak (or raise the hand) as soon as the SPOKEN line is
    complete and some DETAILS have started, then keep the chat message updating until the reply is done.
    Only plain answers go live; anything that drafts a card, or whose spoken numbers don't match the data yet,
    waits for the finished reply."""

    MIN_DETAILS = 24  # characters of DETAILS before committing (proves there is a breakdown to post)
    PUSH_SECONDS = 0.4

    def __init__(self, rt: "MeetingRuntime", participant: str, source: str):
        self.rt, self.participant, self.source = rt, participant, source
        self.mode: str | None = None
        self.message: dict | None = None
        self.pushed = 0.0

    async def update(self, text: str, info: dict) -> None:
        if info.get("cards") or self.mode == "WAIT":
            return
        m = re.search(r"SPOKEN:\s*(.*?)\n\s*DETAILS:\s*\n?(.*)$", text, re.S | re.I)
        if not m:
            return
        spoken, details = m.group(1).strip(), m.group(2).strip()
        if self.mode is None:
            if len(details) < self.MIN_DETAILS or not spoken:
                return
            from .agents import ungrounded_values
            if ungrounded_values(spoken, info.get("outputs", []) + [info.get("command", ""), info.get("context", "")]):
                self.mode = "WAIT"  # let the final reply carry the grounding warning
                return
            humans = self.rt.humans_speaking(exclude=None if self.source == "chat" else self.participant)
            d = etiquette.route(spoken, details, humans, created_card=False)
            self.mode, self.pushed = d.mode, time.time()
            if d.chat:
                self.message = await self.rt.store.post_chat(self.rt.meeting_id, "Tensor", d.chat, kind="markdown", delivery=d.mode, streaming=True)
            if d.mode == "HAND_RAISE":
                await self.rt.store.set_tensor(self.rt.meeting_id, status="hand_raised", hand=dict(
                    raised=True, topic=d.hand_topic, speech=d.pending_speech, message_id=self.message and self.message["id"]))
            elif d.speak:
                asyncio.create_task(self.rt.speak(d.speak))
            return
        if self.message and time.time() - self.pushed >= self.PUSH_SECONDS:
            self.pushed = time.time()
            await self.rt.store.update_chat(self.rt.meeting_id, self.message["id"], f"{spoken}\n\n{details}", streaming=True)

    async def finish(self, result) -> bool:
        """Post the finished text (with any grounding note); True when the answer was already delivered live."""
        if self.mode in (None, "WAIT"):
            return False
        if self.message:
            await self.rt.store.update_chat(self.rt.meeting_id, self.message["id"], f"{result.spoken}\n\n{result.details}".strip(),
                                            streaming=False, tools=result.tools)
        return True


def _cancel(job: dict | None) -> None:
    if job and "cancel" in job:
        job["cancel"].set()


def _strip_approval(text: str) -> str:
    return wake.CANCEL.sub(" ", wake.APPROVE.sub(" ", wake.ADDRESSED.sub(" ", wake.WAKE.sub(" ", text))))


def select_cards(pending: list[dict], utterance: str) -> list[dict]:
    """Which pending card(s) a spoken approval/cancel refers to."""
    text = utterance.lower()
    if " all" in f" {text}" or len(pending) == 1:
        return pending
    if any(w in text for w in ("send", "email", "mail")):
        chosen = [c for c in pending if c["type"] == "EMAIL_DRAFT"]
    elif any(w in text for w in ("ticket", "jira", "create", "task", "plan")):
        chosen = [c for c in pending if c["type"] == "JIRA_TASK_PLAN"]
    else:
        chosen = []
    return chosen or [max(pending, key=lambda c: c.get("updated_at", ""))]
