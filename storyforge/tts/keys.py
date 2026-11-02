"""Trousseau de clés ElevenLabs : clé principale + clés de secours, bascule automatique.

- Clés du .env : ELEVENLABS_API_KEY et ELEVENLABS_API_KEYS (séparées par des virgules).
- Clés ajoutées depuis le bot : enregistrées en base (sur le serveur uniquement).
- Une clé épuisée est mise de côté jusqu'à la remise à zéro de son quota mensuel.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from .. import db
from ..config import Settings

KV_KEYS = "elevenlabs_keys"
KV_EXHAUSTED = "elevenlabs_exhausted"


@dataclass(frozen=True)
class ElevenKey:
    key: str
    source: str  # env | bot

    @property
    def kid(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:10]

    @property
    def masked(self) -> str:
        return f"{self.key[:5]}…{self.key[-4:]}" if len(self.key) > 12 else "…"


class KeyRing:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._bot_keys: list[str] = []
        self._exhausted: dict[str, dict] = {}  # kid -> {"until": ts, "reason": str}
        self.current: str | None = None  # kid de la dernière clé qui a fonctionné

    async def load(self) -> None:
        self._bot_keys = [k for k in (await db.kv_get(KV_KEYS, []) or []) if isinstance(k, str)]
        self._exhausted = dict(await db.kv_get(KV_EXHAUSTED, {}) or {})
        # Les clés du .env sont reprises dans le dashboard : elles y restent même si on les retire du .env.
        new = [k for k in self.env_keys() if k not in self._bot_keys]
        if new:
            self._bot_keys = new + self._bot_keys
            await db.kv_set(KV_KEYS, self._bot_keys)

    def env_keys(self) -> list[str]:
        keys = [self.settings.elevenlabs_api_key] + self.settings.elevenlabs_api_keys.replace(";", ",").split(",")
        return [k.strip() for k in keys if k and k.strip()]

    def all(self) -> list[ElevenKey]:
        seen, out = set(), []
        for key, source in [(k, "bot") for k in self._bot_keys] + [(k, "env") for k in self.env_keys()]:
            if key not in seen:
                seen.add(key)
                out.append(ElevenKey(key, source))
        return out

    def status(self, k: ElevenKey) -> dict | None:
        info = self._exhausted.get(k.kid)
        if info and info.get("until", 0) > time.time():
            return info
        return None

    def available(self) -> list[ElevenKey]:
        keys = [k for k in self.all() if self.status(k) is None]
        # la dernière clé qui a marché en premier : on n'alterne pas pour rien
        keys.sort(key=lambda k: k.kid != self.current)
        return keys

    def has_keys(self) -> bool:
        return bool(self.all())

    async def mark_exhausted(self, k: ElevenKey, until: float, reason: str) -> None:
        self._exhausted[k.kid] = {"until": int(until), "reason": reason}
        await db.kv_set(KV_EXHAUSTED, self._exhausted)
        if self.current == k.kid:
            self.current = None

    async def reactivate(self, kid: str) -> None:
        self._exhausted.pop(kid, None)
        await db.kv_set(KV_EXHAUSTED, self._exhausted)

    async def add(self, key: str) -> ElevenKey:
        key = key.strip()
        if key not in self._bot_keys and key not in self.env_keys():
            self._bot_keys.append(key)
            await db.kv_set(KV_KEYS, self._bot_keys)
        k = ElevenKey(key, "bot" if key not in self.env_keys() else "env")
        await self.reactivate(k.kid)
        return k

    async def remove(self, kid: str) -> bool:
        before = len(self._bot_keys)
        self._bot_keys = [k for k in self._bot_keys if ElevenKey(k, "bot").kid != kid]
        if len(self._bot_keys) != before:
            await db.kv_set(KV_KEYS, self._bot_keys)
            self._exhausted.pop(kid, None)
            await db.kv_set(KV_EXHAUSTED, self._exhausted)
            return True
        return False

    def find(self, kid: str) -> ElevenKey | None:
        return next((k for k in self.all() if k.kid == kid), None)
