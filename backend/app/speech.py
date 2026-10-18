"""Per-participant streaming speech recognition (Speech-to-Text v2) and Tensor's voice (Text-to-Speech).

Each browser sends its own microphone (16 kHz LINEAR16), so speaker identity comes for free.
gRPC streaming runs on a worker thread; results are handed to the event loop. Streams restart
before the 5-minute limit and after silence timeouts.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from typing import Awaitable, Callable

from .config import Settings

log = logging.getLogger(__name__)

STREAM_SECONDS = 280  # restart before the 5-minute streaming limit
BASE_PHRASES = ["Hey Tensor", "Tensor", "go ahead Tensor", "Jira", "story points", "SLA", "send it", "approve all"]
Callback = Callable[[str, bool], Awaitable[None]]  # (text, is_final)


def company_phrases(company: dict | None) -> list[str]:
    """Words worth boosting for this company: its name and the people Tensor may hear about."""
    phrases = list(BASE_PHRASES)
    if company:
        phrases.append(company.get("name", ""))
        for person in company.get("team", []):
            phrases += [person["name"], person["name"].split()[0]]
    return list(dict.fromkeys(p.strip() for p in phrases if p and p.strip()))[:200]


class SpeechStream:
    """One streaming recognizer for one participant's microphone."""

    def __init__(self, settings: Settings, loop: asyncio.AbstractEventLoop, on_result: Callback, client=None, *,
                 model: str | None = None, location: str | None = None, language: str | None = None,
                 phrases: list[str] | None = None, boost: float = 15, interim: bool = True):
        self.settings = settings
        self.loop = loop
        self.on_result = on_result
        self._client = client
        self.model = model or settings.stt_model
        self.location = location or settings.stt_location
        self.language = language or settings.stt_language
        self.phrases = phrases if phrases is not None else BASE_PHRASES
        self.boost = boost
        self.interim = interim
        self.audio: queue.Queue[bytes | None] = queue.Queue(maxsize=400)
        self.closed = False
        self.thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.failures = 0  # consecutive stream failures (reset by any result)

    def push(self, chunk: bytes) -> None:
        if self.closed:
            return
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name=f"stt-{self.model}")
            self.thread.start()
        try:
            self.audio.put_nowait(chunk)
        except queue.Full:
            pass  # drop audio rather than block the socket

    def close(self) -> None:
        self.closed = True
        self.audio.put(None)

    def _client_and_config(self):
        from google.api_core.client_options import ClientOptions
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech as cs

        if self._client is None:
            endpoint = "speech.googleapis.com" if self.location == "global" else f"{self.location}-speech.googleapis.com"
            self._client = SpeechClient(client_options=ClientOptions(api_endpoint=endpoint))
        config = cs.RecognitionConfig(
            explicit_decoding_config=cs.ExplicitDecodingConfig(encoding=cs.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                                                               sample_rate_hertz=16000, audio_channel_count=1),
            language_codes=[self.language], model=self.model,
            features=cs.RecognitionFeatures(enable_automatic_punctuation=True),
            adaptation=cs.SpeechAdaptation(phrase_sets=[cs.SpeechAdaptation.AdaptationPhraseSet(
                inline_phrase_set=cs.PhraseSet(phrases=[cs.PhraseSet.Phrase(value=p, boost=self.boost) for p in self.phrases]))]))
        streaming = cs.StreamingRecognitionConfig(config=config, streaming_features=cs.StreamingRecognitionFeatures(interim_results=self.interim))
        recognizer = f"projects/{self.settings.project}/locations/{self.location}/recognizers/_"
        return self._client, cs, streaming, recognizer

    def _run(self) -> None:
        while not self.closed:
            try:
                client, cs, streaming, recognizer = self._client_and_config()
                started = time.time()

                def requests():
                    yield cs.StreamingRecognizeRequest(recognizer=recognizer, streaming_config=streaming)
                    while time.time() - started < STREAM_SECONDS:
                        try:
                            chunk = self.audio.get(timeout=5)
                        except queue.Empty:
                            return  # silence: end this stream; the next audio starts a new one
                        if chunk is None:
                            return
                        yield cs.StreamingRecognizeRequest(audio=chunk)

                for response in client.streaming_recognize(requests=requests()):
                    self.failures = 0
                    # A response holds a stable part and a still-changing part: interim text is their concatenation.
                    interim = []
                    for result in response.results:
                        text = result.alternatives[0].transcript.strip() if result.alternatives else ""
                        if text and result.is_final:
                            asyncio.run_coroutine_threadsafe(self.on_result(text, True), self.loop)
                        elif text:
                            interim.append(text)
                    if interim:
                        asyncio.run_coroutine_threadsafe(self.on_result(" ".join(interim), False), self.loop)
                if self.audio.empty():
                    return  # stop the thread when idle; push() restarts it
            except Exception as exc:
                self.failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("speech stream (%s) error: %s", self.model, self.last_error)
                time.sleep(1)


class DualSpeech:
    """Two recognizers on the same microphone, each doing what it is best at.

    Measured on noisy Indian- and US-English meeting speech: Chirp 3 with boosted company vocabulary got 0.5% of
    words wrong versus 4.6% for latest_long, but Chirp 3 sends no interim results. So latest_long drives live captions
    and instant wake feedback, and Chirp 3 writes the transcript and the commands. If Chirp 3 keeps failing,
    latest_long's final results are used instead.
    """

    def __init__(self, settings: Settings, loop: asyncio.AbstractEventLoop, on_result: Callback, phrases: list[str] | None = None):
        phrases = phrases or BASE_PHRASES
        self.fast = SpeechStream(settings, loop, self._fast_result, phrases=phrases, boost=15, interim=True)
        self.accurate = SpeechStream(settings, loop, self._accurate_result, model=settings.stt_accurate_model,
                                     location=settings.stt_accurate_location, language=settings.stt_accurate_language,
                                     phrases=phrases, boost=10, interim=False)
        self.on_result = on_result
        self.caption = ""  # fast-model text not yet confirmed by the accurate model

    def push(self, chunk: bytes) -> None:
        self.fast.push(chunk)
        self.accurate.push(chunk)

    def close(self) -> None:
        self.fast.close()
        self.accurate.close()

    @property
    def last_error(self) -> str | None:
        return self.accurate.last_error or self.fast.last_error

    async def _fast_result(self, text: str, final: bool) -> None:
        if self.accurate.failures >= 2:  # accurate model unavailable: fall back to the fast model entirely
            await self.on_result(text, final)
            return
        # Fast finals stay on screen as the caption until the accurate transcript of the same words arrives.
        shown = f"{self.caption} {text}".strip()
        if final:
            self.caption = shown
        await self.on_result(shown, False)

    async def _accurate_result(self, text: str, final: bool) -> None:
        if final:
            self.caption = ""
            await self.on_result(text, True)


PCM_RATE = 24000  # streamed voice: 16-bit mono PCM
STYLE = "Say this like a calm, friendly colleague briefly speaking up in a work meeting:"
# Measured (time to first audio when streamed): Gemini TTS about 1.0 s, Chirp 3 HD 0.2-0.9 s. Gemini sounds more natural.
VOICES = {
    "gemini-achird": dict(label="Achird · warm, natural", engine="Gemini", model="gemini-2.5-flash-tts", name="Achird", lang="en-US"),
    "gemini-kore": dict(label="Kore · clear, confident", engine="Gemini", model="gemini-2.5-flash-tts", name="Kore", lang="en-US"),
    "gemini-aoede": dict(label="Aoede · bright, easy-going", engine="Gemini", model="gemini-2.5-flash-tts", name="Aoede", lang="en-US"),
    "gemini-charon": dict(label="Charon · deep, informative", engine="Gemini", model="gemini-2.5-flash-tts", name="Charon", lang="en-US"),
    "chirp-achird-in": dict(label="Achird · Indian English · fastest", engine="Chirp 3 HD", name="en-IN-Chirp3-HD-Achird", lang="en-IN"),
    "chirp-kore": dict(label="Kore · US English · fastest", engine="Chirp 3 HD", name="en-US-Chirp3-HD-Kore", lang="en-US"),
}
DEFAULT_VOICE = "gemini-achird"


class Voice:
    """Tensor's spoken replies (Cloud Text-to-Speech: Gemini TTS or Chirp 3 HD voices), streamed as they're generated."""

    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from google.cloud import texttospeech as tts

            self._client = tts.TextToSpeechClient()
        return self._client

    @staticmethod
    def _params(voice_id: str | None):
        from google.cloud import texttospeech as tts

        v = VOICES.get(voice_id or "", VOICES[DEFAULT_VOICE])
        return tts.VoiceSelectionParams(language_code=v["lang"], name=v["name"], **({"model_name": v["model"]} if v.get("model") else {})), v

    def stream(self, text: str, voice_id: str | None = None):
        """Yield 24 kHz PCM chunks while the speech is still being generated."""
        from google.cloud import texttospeech as tts

        params, v = self._params(voice_id)
        config = tts.StreamingSynthesizeConfig(voice=params, streaming_audio_config=tts.StreamingAudioConfig(
            audio_encoding=tts.AudioEncoding.PCM, sample_rate_hertz=PCM_RATE))

        def requests():
            yield tts.StreamingSynthesizeRequest(streaming_config=config)
            yield tts.StreamingSynthesizeRequest(input=tts.StreamingSynthesisInput(text=text, **({"prompt": STYLE} if v.get("model") else {})))

        for response in self.client.streaming_synthesize(requests()):
            if response.audio_content:
                yield response.audio_content

    def warm(self, voice_id: str | None = None) -> None:
        """The first stream in a process takes ~3.5 s (connection setup) instead of ~1 s: pay that before anyone asks."""
        try:
            for _ in self.stream("Hi.", voice_id):
                pass
        except Exception as exc:
            log.warning("voice warm-up failed: %s", exc)

    def synthesize(self, text: str, voice_id: str | None = None) -> bytes:
        """Whole MP3 (voice previews, and the fallback when streaming fails)."""
        from google.cloud import texttospeech as tts

        params, v = self._params(voice_id)
        response = self.client.synthesize_speech(
            input=tts.SynthesisInput(text=text, **({"prompt": STYLE} if v.get("model") else {})), voice=params,
            audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3))
        return response.audio_content
