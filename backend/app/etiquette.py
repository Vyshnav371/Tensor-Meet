"""Meeting etiquette: decide whether Tensor speaks, posts to chat, or raises its hand (PRD 3.2 / 5.2)."""
from __future__ import annotations

import re
from dataclasses import dataclass

WORDS_PER_SECOND = 2.5
MAX_SPOKEN_SECONDS = 15.0
CARD_PROMPT = "I've put the draft on screen. Let me know if I should go ahead."


@dataclass(frozen=True)
class Delivery:
    mode: str  # VOICE | VOICE_AND_CHAT | HAND_RAISE | CARD_PROMPT
    speak: str | None  # text to speak now
    chat: str | None  # markdown to post in chat
    hand_topic: str | None = None  # badge text when the hand is raised
    pending_speech: str | None = None  # spoken when a human says "go ahead, Tensor"
    estimated_seconds: float = 0.0


def spoken_seconds(text: str) -> float:
    return len(re.findall(r"\S+", text or "")) / WORDS_PER_SECOND


def has_rich_content(markdown: str) -> bool:
    return bool(re.search(r"^\s*\|.*\|\s*$|```|^\s*([-*]|\d+\.)\s", markdown or "", re.M))


def route(spoken: str, details: str, humans_speaking: bool, created_card: bool) -> Delivery:
    spoken, details = (spoken or "").strip(), (details or "").strip()
    if created_card:
        return Delivery("CARD_PROMPT", CARD_PROMPT, None, estimated_seconds=spoken_seconds(CARD_PROMPT))  # the card is the detail
    full = f"{spoken}\n\n{details}".strip()
    seconds = spoken_seconds(full)
    if not details and seconds <= MAX_SPOKEN_SECONDS and not has_rich_content(spoken):
        return Delivery("VOICE", spoken, None, estimated_seconds=seconds)
    chat = full if details else spoken
    if humans_speaking:
        headline = re.split(r"(?<=[.!?])\s", spoken, maxsplit=1)[0]
        topic = headline if len(headline) <= 70 else headline[:67].rsplit(" ", 1)[0] + "…"
        return Delivery("HAND_RAISE", None, chat, hand_topic=topic, pending_speech=_tldr(spoken), estimated_seconds=seconds)
    return Delivery("VOICE_AND_CHAT", _tldr(spoken) + " I've posted the full breakdown in the chat.", chat, estimated_seconds=seconds)


def _tldr(spoken: str) -> str:
    """One sentence, capped well under the 15-second limit."""
    first = re.split(r"(?<=[.!?])\s", spoken.strip(), maxsplit=1)[0]
    words = first.split()
    return " ".join(words[:22]) + ("…" if len(words) > 22 else "")
