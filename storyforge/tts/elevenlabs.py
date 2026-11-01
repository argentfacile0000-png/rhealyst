"""Synthèse vocale ElevenLabs avec horodatage caractère par caractère (pour les sous-titres)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import httpx

from ..video.ffmpeg import run_ffmpeg, to_wav
from .keys import ElevenKey, KeyRing
from .timing import Word, concat_wavs, scale_words, tighten_pauses, wav_duration, words_from_alignment

log = logging.getLogger(__name__)

API_BASE = "https://api.elevenlabs.io"
T = TypeVar("T")
MAX_CHARS_PER_REQUEST = 2400


class ElevenLabsError(RuntimeError):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code  # quota_exceeded, invalid_api_key, voice_not_found, missing_permissions…


# Erreurs propres à une clé : on essaie la clé suivante.
KEY_ERRORS = ("quota_exceeded", "invalid_api_key", "voice_not_found", "missing_permissions")


@dataclass
class VoiceSettings:
    stability: float = 0.45
    similarity_boost: float = 0.8
    style: float = 0.2
    speed: float = 1.1
    use_speaker_boost: bool = True

    def to_api(self) -> dict:
        return {
            "stability": round(min(max(self.stability, 0.0), 1.0), 2),
            "similarity_boost": round(min(max(self.similarity_boost, 0.0), 1.0), 2),
            "style": round(min(max(self.style, 0.0), 1.0), 2),
            "use_speaker_boost": self.use_speaker_boost,
            "speed": round(min(max(self.speed, 0.7), 1.2), 2),
        }


@dataclass
class TTSResult:
    audio_path: Path
    words: list[Word]
    duration: float
    characters: int


def split_for_tts(text: str, max_chars: int = MAX_CHARS_PER_REQUEST) -> list[str]:
    """Découpe aux frontières de phrases pour rester sous la limite d'une requête."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > max_chars:  # phrase anormalement longue
            cut = s.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(s[:cut].strip())
            s = s[cut:].strip()
        if len(cur) + len(s) + 1 > max_chars and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def _explain_error(resp: httpx.Response) -> ElevenLabsError:
    try:
        detail = resp.json().get("detail")
    except (json.JSONDecodeError, ValueError, AttributeError):
        detail = resp.text[:300]
    if isinstance(detail, dict):
        status = detail.get("status", "")
        msg = detail.get("message", "")
        if status == "quota_exceeded":
            return ElevenLabsError("Quota de caractères ElevenLabs épuisé", "quota_exceeded")
        if status == "missing_permissions":
            return ElevenLabsError("Permission manquante sur la clé ElevenLabs", "missing_permissions")
        if status in ("invalid_api_key", "needs_authorization") or resp.status_code == 401:
            return ElevenLabsError("Clé API ElevenLabs invalide", "invalid_api_key")
        if status == "voice_not_found":
            return ElevenLabsError("Voix ElevenLabs introuvable (vérifie l'ID de voix)", "voice_not_found")
        return ElevenLabsError(f"ElevenLabs {resp.status_code}: {status} {msg}".strip(), status)
    if resp.status_code == 401:
        return ElevenLabsError("Clé API ElevenLabs invalide", "invalid_api_key")
    return ElevenLabsError(f"ElevenLabs {resp.status_code}: {detail}")


class ElevenLabsClient:
    def __init__(self, api_key: str = "", model_id: str = "eleven_multilingual_v2", *, ffmpeg: str = "ffmpeg",
                 timeout: float = 180, keys: KeyRing | None = None):
        self.api_key = api_key
        self.model_id = model_id
        self.ffmpeg = ffmpeg
        self.timeout = timeout
        self.keys = keys  # trousseau (clés de secours) ; sinon api_key seule
        self.on_event: Callable[[str], Awaitable[None]] | None = None  # prévenir lors d'une bascule de clé

    async def _request(self, method: str, path: str, *, api_key: str, **kwargs) -> dict:
        if not api_key:
            raise ElevenLabsError("ELEVENLABS_API_KEY manquant dans le .env", "invalid_api_key")
        headers = {"xi-api-key": api_key, "Accept": "application/json"}
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(base_url=API_BASE, timeout=self.timeout) as client:
                    resp = await client.request(method, path, headers=headers, **kwargs)
                if resp.status_code == 429 or resp.status_code >= 500:
                    err = _explain_error(resp)
                    if err.code == "quota_exceeded":
                        raise err
                    last_exc = err
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    raise _explain_error(resp)
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                await asyncio.sleep(3 * (attempt + 1))
        raise ElevenLabsError(f"ElevenLabs injoignable: {last_exc}")

    def _candidates(self) -> list[ElevenKey]:
        if self.keys is None:
            return [ElevenKey(self.api_key, "env")]
        if not self.keys.has_keys():
            raise ElevenLabsError("Aucune clé ElevenLabs : ajoute ELEVENLABS_API_KEY ou une clé depuis le bot")
        keys = self.keys.available()
        if not keys:
            raise ElevenLabsError("Toutes les clés ElevenLabs sont épuisées ou invalides : ajoute une clé depuis le bot",
                                  "quota_exceeded")
        return keys

    async def _reset_time(self, api_key: str) -> float:
        try:
            sub = await self._request("GET", "/v1/user/subscription", api_key=api_key)
            reset = float(sub.get("next_character_count_reset_unix") or 0)
            if reset > time.time():
                return reset
        except ElevenLabsError:
            pass
        return time.time() + 86400

    async def _with_keys(self, call: Callable[[str], Awaitable[T]]) -> T:
        """Exécute l'appel avec la première clé disponible, bascule sur la suivante si elle est épuisée."""
        candidates = self._candidates()
        last: ElevenLabsError | None = None
        for i, k in enumerate(candidates):
            try:
                result = await call(k.key)
                if self.keys:
                    self.keys.current = k.kid
                return result
            except ElevenLabsError as exc:
                if self.keys is None or exc.code not in KEY_ERRORS:
                    raise
                last = exc
                if exc.code in ("quota_exceeded", "invalid_api_key"):
                    until = await self._reset_time(k.key) if exc.code == "quota_exceeded" else time.time() + 365 * 86400
                    await self.keys.mark_exhausted(k, until, exc.code)
                    nxt = candidates[i + 1].masked if i + 1 < len(candidates) else None
                    if self.on_event:
                        reason = "épuisée" if exc.code == "quota_exceeded" else "invalide"
                        await self.on_event(f"Clé ElevenLabs {k.masked} {reason}"
                                            + (f" : passage à la clé {nxt}." if nxt else " : plus aucune clé disponible."))
        if last and len(candidates) > 1 and last.code in ("quota_exceeded", "invalid_api_key"):
            raise ElevenLabsError("Toutes les clés ElevenLabs sont épuisées ou invalides : ajoute une clé depuis le bot",
                                  last.code)
        raise last or ElevenLabsError("Aucune clé ElevenLabs utilisable")

    async def list_voices(self) -> list[dict]:
        data = await self._with_keys(lambda key: self._request("GET", "/v1/voices", api_key=key))
        voices = []
        for v in data.get("voices", []):
            labels = v.get("labels") or {}
            voices.append(
                {
                    "voice_id": v.get("voice_id"),
                    "name": v.get("name", "?"),
                    "category": v.get("category", ""),
                    "gender": labels.get("gender", ""),
                    "accent": labels.get("accent", ""),
                    "age": labels.get("age", ""),
                    "description": labels.get("description", "") or labels.get("descriptive", ""),
                    "preview_url": v.get("preview_url"),
                }
            )
        return voices

    async def subscription(self, api_key: str | None = None) -> dict:
        if api_key:
            return await self._request("GET", "/v1/user/subscription", api_key=api_key)
        return await self._with_keys(lambda key: self._request("GET", "/v1/user/subscription", api_key=key))

    async def _tts_chunk(self, text: str, voice_id: str, settings: VoiceSettings, prev_text: str, next_text: str) -> dict:
        body: dict = {"text": text, "model_id": self.model_id, "voice_settings": settings.to_api()}
        if prev_text:
            body["previous_text"] = prev_text[-400:]
        if next_text:
            body["next_text"] = next_text[:300]
        return await self._with_keys(lambda key: self._request(
            "POST",
            f"/v1/text-to-speech/{voice_id}/with-timestamps",
            api_key=key,
            params={"output_format": "mp3_44100_128"},
            json=body,
        ))

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        settings: VoiceSettings,
        workdir: Path,
        *,
        speedup: float = 1.0,
        max_pause: float = 0.22,
    ) -> TTSResult:
        """Texte -> narration.wav finale (silences resserrés, accélérée, normalisée) + mots horodatés."""
        workdir.mkdir(parents=True, exist_ok=True)
        chunks = split_for_tts(text)
        wavs: list[Path] = []
        chunk_words: list[list[Word]] = []
        for i, chunk in enumerate(chunks):
            data = await self._tts_chunk(
                chunk, voice_id, settings,
                prev_text=chunks[i - 1] if i else "",
                next_text=chunks[i + 1] if i + 1 < len(chunks) else "",
            )
            audio_b64 = data.get("audio_base64")
            if not audio_b64:
                raise ElevenLabsError("Réponse ElevenLabs sans audio")
            mp3 = workdir / f"tts_{i:02d}.mp3"
            mp3.write_bytes(base64.b64decode(audio_b64))
            wav = workdir / f"tts_{i:02d}.wav"
            await to_wav(mp3, wav, ffmpeg=self.ffmpeg)
            wavs.append(wav)
            al = data.get("alignment") or data.get("normalized_alignment") or {}
            chunk_words.append(
                words_from_alignment(
                    al.get("characters", []),
                    al.get("character_start_times_seconds", []),
                    al.get("character_end_times_seconds", []),
                )
            )

        raw = workdir / "narration_raw.wav"
        offsets = concat_wavs(wavs, raw)
        words: list[Word] = []
        for off, ws in zip(offsets, chunk_words):
            words += [Word(w.text, w.start + off, w.end + off) for w in ws]
        if not words:
            raise ElevenLabsError("Aucun horodatage reçu d'ElevenLabs")

        tight = workdir / "narration_tight.wav"
        words = tighten_pauses(raw, tight, words, max_pause=max_pause)

        final = workdir / "narration.wav"
        speed = min(max(speedup, 0.5), 2.0)
        filters = []
        if abs(speed - 1.0) > 0.001:
            filters.append(f"atempo={speed:.3f}")
        filters += ["loudnorm=I=-14:TP=-1.5:LRA=11", "aresample=44100"]
        await run_ffmpeg(
            ["-i", str(tight), "-af", ",".join(filters), "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(final)],
            ffmpeg=self.ffmpeg,
        )
        words = scale_words(words, speed)
        duration = wav_duration(final)
        return TTSResult(audio_path=final, words=words, duration=duration, characters=len(text))
