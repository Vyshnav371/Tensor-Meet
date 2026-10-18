"""End-to-end demo run: the three PRD flows, spoken through the real speech pipeline.

Two simulated participants join over WebSocket. Their voices are synthesized with Text-to-Speech and
streamed in real time (16 kHz PCM, 100 ms frames) into Speech-to-Text, exactly like a browser mic.
Usage: python scripts/demo_e2e.py [--base http://127.0.0.1:8000] [--meeting demo-e2e-<n>]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
import websockets
from google.cloud import texttospeech as tts

VOICE = "en-US-Chirp3-HD-Charon"
SILENCE = b"\x00\x00" * 1600  # 100 ms


def synth(client, text: str) -> bytes:
    audio = client.synthesize_speech(input=tts.SynthesisInput(text=text), voice=tts.VoiceSelectionParams(language_code="en-US", name=VOICE),
                                     audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.LINEAR16, sample_rate_hertz=16000)).audio_content
    return audio[44:]


class Participant:
    def __init__(self, base: str, meeting: str, name: str, log: list):
        self.url = base.replace("http", "ws") + f"/ws/{meeting}?name={name}"
        self.name, self.log, self.events = name, log, []

    async def __aenter__(self):
        self.ws = await websockets.connect(self.url, max_size=None)
        self.reader = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *exc):
        self.reader.cancel()
        await self.ws.close()

    async def _read(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            self.events.append((time.time(), msg["event"], msg["data"]))
            if self.name == "Vyshnav" and msg["event"] in ("tensor", "speak", "card", "chat"):
                d = msg["data"]
                summary = {"tensor": lambda: f"status={d.get('status')}" + (f" hand={d['hand']['topic']!r}" if d.get("hand") else ""),
                           "speak": lambda: f"says {d['text']!r} (audio {'yes' if d.get('audio_mp3_base64') else 'no'})",
                           "card": lambda: f"{d['type']} {d['status']} {(d.get('result') or {}).get('refs', '')}",
                           "chat": lambda: f"{d['author']}: {d['text'][:90]!r}"}[msg["event"]]()
                self.log.append(f"  [{time.strftime('%H:%M:%S')}] {msg['event']}: {summary}")

    async def say(self, pcm: bytes, trailing_silence: float = 1.2):
        started = time.time()
        for i in range(0, len(pcm), 3200):
            await self.ws.send(pcm[i:i + 3200])
            await asyncio.sleep(0.1)
        for _ in range(int(trailing_silence * 10)):
            await self.ws.send(SILENCE)
            await asyncio.sleep(0.1)
        return started

    async def vad(self, speaking: bool):
        await self.ws.send(json.dumps(dict(type="vad", speaking=speaking)))

    def first_after(self, t0: float, event: str, pred=lambda d: True):
        return next(((t, d) for t, e, d in self.events if t >= t0 and e == event and pred(d)), None)

    async def wait_for(self, t0, event, pred=lambda d: True, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            hit = self.first_after(t0, event, pred)
            if hit:
                return hit
            await asyncio.sleep(0.2)
        raise TimeoutError(f"{self.name}: no {event} after {timeout}s")


async def main(base: str, meeting: str):
    client = tts.TextToSpeechClient()
    lines = {
        "q1": "Hey Tensor, does this spec conflict with our internal cloud security guidelines?",
        "chatter": "While that runs, let's talk about the launch checklist for December and who owns the load test.",
        "go": "Go ahead, Tensor.",
        "q2": "Hey Tensor, turn the TLS and audit log conflicts into Jira tickets for the security sprint and assign them to David.",
        "edit": "Change the first one to Highest priority and go ahead.",
        "q3": "Hey Tensor, email David the links to those new tickets with a short summary.",
        "send": "Send it.",
    }
    audio = {k: await asyncio.to_thread(synth, client, v) for k, v in lines.items()}
    log: list[str] = []
    results = {}
    async with Participant(base, meeting, "Vyshnav", log) as me, Participant(base, meeting, "Sarah", []) as sarah:
        await asyncio.sleep(1)
        async with httpx.AsyncClient(base_url=base, timeout=60) as http:
            pdf = Path(__file__).resolve().parents[2] / "demo" / "Q4_Architecture_Spec.pdf"
            r = await http.post(f"/api/meetings/{meeting}/documents", data=dict(by="Vyshnav"), files=dict(file=(pdf.name, pdf.read_bytes(), "application/pdf")))
            r.raise_for_status()
            t_upload = time.time()
            _, doc = await me.wait_for(t_upload - 1, "document", lambda d: d["status"] != "PARSING", 90)
            results["docai"] = f"{doc['status']} tables={doc.get('tables')} in {time.time() - t_upload:.1f}s"

            # Flow 1: question, then humans keep talking -> hand raise + chat; then "Go ahead, Tensor".
            t0 = await me.say(audio["q1"], trailing_silence=0.6)
            sarah_talk = asyncio.create_task(sarah.say(audio["chatter"]))
            await sarah.vad(True)
            t_listen, _ = await me.wait_for(t0, "tensor", lambda d: d.get("status") == "listening", 30)
            results["wake_latency_s_from_utterance_start"] = round(t_listen - t0, 2)
            keep_talking = True

            async def chatter_vad():
                while keep_talking:
                    await sarah.vad(True)
                    await asyncio.sleep(0.7)
            vad_task = asyncio.create_task(chatter_vad())
            t_hand, hand = await me.wait_for(t0, "tensor", lambda d: d.get("status") in ("hand_raised", "speaking"), 150)
            keep_talking = False
            vad_task.cancel()
            await sarah_talk
            results["flow1"] = f"{hand.get('status')} after {t_hand - t0:.1f}s; topic={((hand.get('hand') or {}).get('topic'))!r}"
            await sarah.vad(False)
            await asyncio.sleep(2)
            t_go = await me.say(audio["go"])
            _, spoken = await me.wait_for(t_go, "speak", timeout=30)
            results["flow1_go_ahead"] = spoken["text"]

            # Flow 2: Jira plan card, then edit + approve in one sentence (no wake word).
            t2 = await me.say(audio["q2"])
            t_card, card = await me.wait_for(t2, "card", lambda d: d["type"] == "JIRA_TASK_PLAN" and d["status"] == "PENDING_REVIEW", 150)
            results["flow2_card"] = f"{len(card['payload']['proposed_tasks'])} tasks in {t_card - t2:.1f}s: " + "; ".join(
                f"{t['title'][:50]} [{t['priority']}, {t['story_points']}pts, {t['assignee']}]" for t in card["payload"]["proposed_tasks"])
            await asyncio.sleep(4)
            t_edit = await me.say(audio["edit"])
            t_done, done = await me.wait_for(t_edit, "card", lambda d: d["card_id"] == card["card_id"] and d["status"] in ("EXECUTED", "FAILED"), 120)
            results["flow2_executed"] = f"{done['status']} {done.get('result', {}).get('refs')} first priority={done['payload']['proposed_tasks'][0]['priority']} via {done.get('approval_channel')} in {t_done - t_edit:.1f}s"

            # Flow 3: email card with the ticket links, then "Send it."
            await asyncio.sleep(4)
            t3 = await me.say(audio["q3"])
            t_mail, mail = await me.wait_for(t3, "card", lambda d: d["type"] == "EMAIL_DRAFT" and d["status"] == "PENDING_REVIEW", 150)
            results["flow3_draft"] = f"in {t_mail - t3:.1f}s to {[c['name'] for c in mail['payload']['to']]}: {mail['payload']['subject']!r}; body mentions refs: {all(r in mail['payload']['body'] for r in done.get('result', {}).get('refs', []))}"
            await asyncio.sleep(4)
            t_send = await me.say(audio["send"])
            _, sent = await me.wait_for(t_send, "card", lambda d: d["card_id"] == mail["card_id"] and d["status"] in ("EXECUTED", "FAILED"), 60)
            results["flow3_sent"] = f"{sent['status']} via {sent.get('approval_channel')} executor={sent.get('result', {}).get('executor')}"

            transcript = (await http.get(f"/api/meetings/{meeting}")).json()["transcript"]
            results["transcript"] = [f"{s['speaker']}: {s['text']}" for s in transcript]
    print("\n".join(log))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--meeting", default=f"demo-e2e-{int(time.time()) % 100000}")
    args = p.parse_args()
    asyncio.run(main(args.base, args.meeting))
