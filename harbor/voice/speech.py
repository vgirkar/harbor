"""Speech providers (STT + TTS) over plain HTTP, no vendor SDKs.

elevenlabs : Scribe (speech to text) + Flash v2.5 (text to speech), one key for both
openai     : gpt-4o-mini-transcribe + gpt-4o-mini-tts
The voice endpoint is transcribe -> same LangGraph turn as text -> synthesize, so voice never forks the logic.
"""

from __future__ import annotations

from typing import Protocol

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from harbor.config import Settings


class SpeechProvider(Protocol):
    name: str

    async def transcribe(self, audio: bytes, mime_type: str) -> str: ...

    async def synthesize(self, text: str) -> tuple[bytes, str]: ...


_retry = retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    reraise=True,
)


class ElevenLabsSpeech:
    name = "elevenlabs"

    def __init__(self, api_key: str, voice_id: str, tts_model: str, stt_model: str):
        self._headers = {"xi-api-key": api_key}
        self._voice = voice_id
        self._tts_model = tts_model
        self._stt_model = stt_model
        self._client = httpx.AsyncClient(base_url="https://api.elevenlabs.io", timeout=60)

    @_retry
    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        res = await self._client.post(
            "/v1/speech-to-text",
            headers=self._headers,
            files={"file": ("audio", audio, mime_type)},
            data={"model_id": self._stt_model},
        )
        res.raise_for_status()
        return res.json().get("text", "").strip()

    @_retry
    async def synthesize(self, text: str) -> tuple[bytes, str]:
        res = await self._client.post(
            f"/v1/text-to-speech/{self._voice}",
            params={"output_format": "mp3_44100_128"},
            headers={**self._headers, "accept": "audio/mpeg"},
            json={"text": text, "model_id": self._tts_model},
        )
        res.raise_for_status()
        return res.content, "audio/mpeg"


class OpenAISpeech:
    name = "openai"

    def __init__(self, api_key: str, tts_model: str, stt_model: str, voice: str = "alloy"):
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._tts_model, self._stt_model, self._voice = tts_model, stt_model, voice
        self._client = httpx.AsyncClient(base_url="https://api.openai.com", timeout=60)

    @_retry
    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        res = await self._client.post(
            "/v1/audio/transcriptions",
            headers=self._headers,
            files={"file": ("audio.webm", audio, mime_type)},
            data={"model": self._stt_model},
        )
        res.raise_for_status()
        return res.json().get("text", "").strip()

    @_retry
    async def synthesize(self, text: str) -> tuple[bytes, str]:
        res = await self._client.post(
            "/v1/audio/speech",
            headers=self._headers,
            json={"model": self._tts_model, "voice": self._voice, "input": text, "response_format": "mp3"},
        )
        res.raise_for_status()
        return res.content, "audio/mpeg"


class FakeSpeech:
    name = "fake"

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        try:
            return audio.decode("utf-8").strip()
        except UnicodeDecodeError:
            return "hello"

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        return b"ID3" + text.encode("utf-8"), "audio/mpeg"


def build_speech(settings: Settings) -> SpeechProvider:
    if settings.speech_provider == "elevenlabs":
        if not settings.elevenlabs_api_key:
            raise RuntimeError("SPEECH_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY")
        return ElevenLabsSpeech(
            settings.elevenlabs_api_key,
            settings.elevenlabs_voice_id,
            settings.elevenlabs_tts_model,
            settings.elevenlabs_stt_model,
        )
    if settings.speech_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("SPEECH_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAISpeech(settings.openai_api_key, settings.openai_tts_model, settings.openai_stt_model)
    return FakeSpeech()
