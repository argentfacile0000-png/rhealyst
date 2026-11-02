"""Trousseau de clés d'IA (écriture des histoires), géré depuis le dashboard Telegram.

- Plusieurs clés, de fournisseurs différents (Gemini, Groq, OpenAI, OpenRouter), essayées dans l'ordre.
- Une clé refusée (invalide, quota atteint, limite de débit) est mise de côté et la suivante prend le relais.
- Les clés du .env (GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY) restent prises en compte, en premier.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from .. import db
from ..config import LLM_PROVIDERS, LLMConfig, Settings

KV_KEYS = "llm_keys"  # [{"key": ..., "provider": ...}] dans l'ordre d'essai
KV_COOLDOWN = "llm_cooldown"  # kid -> {"until": ts, "reason": str}

PROVIDERS: dict[str, dict] = {
    **LLM_PROVIDERS,
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "model": "meta-llama/llama-3.3-70b-instruct:free", "key": "", "env": "", "free": True},
    # Amazon Bedrock (clé API Bedrock) : API Converse, valable pour tous les modèles du catalogue Bedrock
    "bedrock": {"label": "AWS Bedrock", "base_url": "", "model": "anthropic.claude-sonnet-5-5",
                "key": "", "env": "", "free": False},
}

# Modèles proposés quand la liste du fournisseur ne peut pas être lue (« Autre modèle » permet tout ID).
SUGGESTED_MODELS: dict[str, list[str]] = {
    "gemini": ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-flash-lite-latest"],
    "groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b"],
    "openai": ["gpt-4.1", "gpt-4.1-mini", "gpt-5-mini", "gpt-5"],
    "openrouter": ["meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-oss-120b", "deepseek/deepseek-chat"],
    "bedrock": ["anthropic.claude-sonnet-5-5", "anthropic.claude-haiku-5-5", "anthropic.claude-opus-5-5",
                "amazon.nova-pro-v1:0", "meta.llama3-3-70b-instruct-v1:0", "openai.gpt-oss-120b-1:0"],
}
BEDROCK_REGIONS = [("us-east-1", "Virginie"), ("us-west-2", "Oregon"), ("eu-west-3", "Paris"),
                   ("eu-central-1", "Francfort"), ("eu-west-1", "Irlande")]
INVALID_FOR = 10 * 365 * 86400  # clé invalide : écartée jusqu'à réactivation manuelle


def detect_provider(key: str) -> str | None:
    key = key.strip()
    if key.startswith(("ABSK", "bedrock-api-key-")):
        return "bedrock"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("gsk_"):
        return "groq"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("sk-"):
        return "openai"
    return None


@dataclass(frozen=True)
class LLMKey:
    key: str
    provider: str
    source: str  # env | bot
    model: str = ""  # modèle choisi (vide = modèle par défaut du fournisseur)
    region: str = ""  # région AWS (Bedrock)

    @property
    def kid(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:10]

    @property
    def masked(self) -> str:
        return f"{self.key[:6]}…{self.key[-4:]}" if len(self.key) > 14 else "…"

    @property
    def label(self) -> str:
        return PROVIDERS[self.provider]["label"]

    def config(self, settings: Settings) -> LLMConfig:
        p = PROVIDERS[self.provider]
        if self.provider == "bedrock":
            region = self.region or "us-east-1"
            return LLMConfig("bedrock", p["label"], f"https://bedrock-runtime.{region}.amazonaws.com",
                             self.model or p["model"], self.key, "", False)
        if self.provider == "openai":
            model = self.model or settings.llm_model or settings.openai_model
            base = settings.openai_base_url or p["base_url"]
        else:
            same = (settings.llm_provider or "auto").lower() == self.provider
            model = self.model or (settings.llm_model if (settings.llm_model and same) else p["model"])
            base = p["base_url"]
        return LLMConfig(self.provider, p["label"], base, model, self.key, p.get("env") or "", p["free"])


class LLMKeyRing:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._bot: list[dict] = []
        self._cooldown: dict[str, dict] = {}
        self.current: str | None = None  # kid de la dernière clé qui a fonctionné

    async def load(self) -> None:
        self._bot = [d for d in (await db.kv_get(KV_KEYS, []) or [])
                     if isinstance(d, dict) and d.get("key") and d.get("provider") in PROVIDERS]
        self._cooldown = dict(await db.kv_get(KV_COOLDOWN, {}) or {})
        # Les clés du .env sont reprises dans le dashboard : elles y restent même si on les retire du .env.
        new = [{"key": k.key, "provider": k.provider} for k in self.env_keys()
               if not any(d["key"] == k.key for d in self._bot)]
        if new:
            self._bot = new + self._bot
            await db.kv_set(KV_KEYS, self._bot)

    def env_keys(self) -> list[LLMKey]:
        out = []
        for name in ("gemini", "groq", "openai"):
            key = getattr(self.settings, LLM_PROVIDERS[name]["key"], "")
            if key:
                out.append(LLMKey(key.strip(), name, "env"))
        return out

    def all(self) -> list[LLMKey]:
        seen, out = set(), []
        for k in [LLMKey(d["key"], d["provider"], "bot", d.get("model", ""), d.get("region", "")) for d in self._bot] \
                + self.env_keys():
            if k.key not in seen:
                seen.add(k.key)
                out.append(k)
        return out

    def status(self, k: LLMKey) -> dict | None:
        info = self._cooldown.get(k.kid)
        return info if info and info.get("until", 0) > time.time() else None

    def available(self) -> list[LLMKey]:
        keys = [k for k in self.all() if not self.status(k)]
        if self.current:  # la dernière clé qui a marché passe devant
            keys.sort(key=lambda k: k.kid != self.current)
        return keys

    def get(self, kid: str) -> LLMKey | None:
        return next((k for k in self.all() if k.kid == kid), None)

    async def update(self, kid: str, **fields: str) -> LLMKey | None:
        """Change le modèle ou la région d'une clé du dashboard."""
        for d in self._bot:
            if LLMKey(d["key"], d["provider"], "bot").kid == kid:
                d.update({k: v for k, v in fields.items() if k in ("model", "region")})
                await db.kv_set(KV_KEYS, self._bot)
                self._cooldown.pop(kid, None)  # nouveau modèle : on lui redonne sa chance
                await db.kv_set(KV_COOLDOWN, self._cooldown)
                return self.get(kid)
        return None

    async def add(self, key: str, provider: str, *, model: str = "", region: str = "") -> LLMKey:
        key = key.strip()
        if not any(d["key"] == key for d in self._bot):
            entry = {"key": key, "provider": provider}
            if model:
                entry["model"] = model
            if region:
                entry["region"] = region
            self._bot.append(entry)
            await db.kv_set(KV_KEYS, self._bot)
        self._cooldown.pop(LLMKey(key, provider, "bot").kid, None)
        await db.kv_set(KV_COOLDOWN, self._cooldown)
        return LLMKey(key, provider, "bot", model, region)

    async def remove(self, kid: str) -> None:
        self._bot = [d for d in self._bot if LLMKey(d["key"], d["provider"], "bot").kid != kid]
        await db.kv_set(KV_KEYS, self._bot)

    async def move_up(self, kid: str) -> None:
        idx = next((i for i, d in enumerate(self._bot) if LLMKey(d["key"], d["provider"], "bot").kid == kid), None)
        if idx:
            self._bot[idx - 1], self._bot[idx] = self._bot[idx], self._bot[idx - 1]
            await db.kv_set(KV_KEYS, self._bot)

    async def mark_failed(self, k: LLMKey, seconds: float, reason: str) -> None:
        self._cooldown[k.kid] = {"until": time.time() + seconds, "reason": reason}
        if self.current == k.kid:
            self.current = None
        await db.kv_set(KV_COOLDOWN, self._cooldown)

    async def reactivate(self, kid: str) -> None:
        self._cooldown.pop(kid, None)
        await db.kv_set(KV_COOLDOWN, self._cooldown)
