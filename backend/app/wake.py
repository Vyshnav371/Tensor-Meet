"""Wake-word, follow-up and approval detection on transcript text (deterministic, no LLM).

Speech recognizers spell the wake word many ways ("hey tensor", "hey tenser", "hey ten sir",
"a tensor"), so matching is phonetic-ish on normalized text. Approval is detected here, in code:
an AI model never decides that a human approved something.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TENSOR = r"(?:tensor|tenser|tensar|tensir|tensa|tense[\s-]?or|tenzor|tenzer|ten[\s-]?(?:sir|sor|zer|zor)|tencer)"
_SEPARATORS = r'''[\s,.!?:;"'“”‘’—–-]*'''
# "Hey dancer" is a known STT mishearing. Require a greeting for this ambiguous alias.
WAKE = re.compile(rf"\b(?:(?:hey|hi|hello|hay|ok(?:ay)?|yo){_SEPARATORS}(?:{_TENSOR}|dancer)|a{_SEPARATORS}{_TENSOR})\b{_SEPARATORS}", re.I)
ADDRESSED = re.compile(rf"^{_SEPARATORS}{_TENSOR}\b{_SEPARATORS}", re.I)  # "Tensor, ..." at the start
GO_AHEAD_TENSOR = re.compile(rf"\b(?:go ahead|let'?s hear it|you can speak|go on)[\s,]*{_TENSOR}\b|\b{_TENSOR}[\s,]*(?:go ahead|go on)\b", re.I)

APPROVE = re.compile(r"\b(go ahead|send it|send (?:the )?(?:email|mail)|approve(?:d)?(?: all| it| them)?|create (?:them|it|the tickets)|"
                     r"ship it|do it|looks good,? (?:send|create|go)|confirm(?:ed)?)\b", re.I)
CANCEL = re.compile(r"\b(cancel|discard|scrap|don'?t send|do not send|never ?mind|drop (?:it|that|the draft))\b", re.I)
EDIT = re.compile(r"\b(change|make|set|assign|reassign|cc|bcc|add|remove|drop|rename|update|move|increase|decrease|"
                  r"priority|story points?|points|subject|formal|shorter|longer|tone|instead)\b", re.I)


@dataclass(frozen=True)
class WakeMatch:
    command: str  # text after the wake phrase ("" if the speaker paused after "Hey Tensor")


def detect_wake(text: str) -> WakeMatch | None:
    m = WAKE.search(text) or ADDRESSED.search(text)
    if not m:
        return None
    return WakeMatch(command=text[m.end():].strip())


def wants_raised_answer(text: str) -> bool:
    """'Go ahead, Tensor' / 'Tensor, go ahead' when Tensor has its hand raised."""
    return bool(GO_AHEAD_TENSOR.search(text))


def is_approval(text: str) -> bool:
    return bool(APPROVE.search(text)) and not CANCEL.search(text)


def is_cancel(text: str) -> bool:
    return bool(CANCEL.search(text))


def is_card_followup(text: str) -> bool:
    """Utterances that plausibly address a pending draft card without the wake word."""
    return bool(APPROVE.search(text) or CANCEL.search(text) or EDIT.search(text))


def chat_mention(text: str) -> str | None:
    m = re.match(r"^\s*@tensor\b[\s,:]*", text, re.I)
    return text[m.end():].strip() if m else None
