"""Clés d'IA et ElevenLabs gérées depuis le dashboard, avec bascule automatique."""

from __future__ import annotations

import httpx
import openai
import pytest

from storyforge import db
from storyforge.story import generator as gen_mod
from storyforge.story.llm_keys import LLMKey, LLMKeyRing, detect_provider

from .conftest import STORY_JSON, fake_openai, needs_ffmpeg
from .test_bot import click, make_ctx, type_text

pytestmark = needs_ffmpeg
GEMINI = "AIzaSyA-gemini-key-aaaaaaaaaaaaaaaaaaaa"
GROQ = "gsk_groq_key_bbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_detect_provider():
    assert detect_provider(GEMINI) == "gemini" and detect_provider(GROQ) == "groq"
    assert detect_provider("sk-or-v1-abc") == "openrouter" and detect_provider("sk-proj-abc") == "openai"
    assert detect_provider("bonjour") is None


def _rate_limit():
    resp = httpx.Response(429, request=httpx.Request("POST", "https://x"), json={"error": {"message": "quota"}})
    return openai.RateLimitError("Resource has been exhausted (quota)", response=resp, body=None)


async def test_story_falls_back_to_the_next_key(svc, monkeypatch):
    svc.settings.openai_api_key = ""
    ring = svc.pipeline.llm_keys
    await db.kv_set("llm_keys", [])
    await ring.load()
    await ring.add(GEMINI, "gemini")
    await ring.add(GROQ, "groq")
    used = []

    class Client:
        def __init__(self, api_key, base_url=None, max_retries=0):
            self.key = api_key
            ok = fake_openai([STORY_JSON] * 3)
            outer = self

            class Completions:
                async def create(self, **kw):
                    used.append((outer.key[:4], kw["model"]))
                    if outer.key == GEMINI:
                        raise _rate_limit()
                    return await ok.chat.completions.create(**kw)

            self.chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(gen_mod, "AsyncOpenAI", Client)
    gen = svc.pipeline.stories
    gen._client = None
    events = []

    async def on_event(text):
        events.append(text)

    gen.on_event = on_event
    story = await gen.generate(language="fr", seconds_per_part=6)
    assert story.title and used[0][0] == "AIza" and used[1] == ("gsk_", "llama-3.3-70b-versatile")
    assert story.model == "llama-3.3-70b-versatile"
    assert events and "passage à Groq" in events[0]
    gemini = next(k for k in ring.all() if k.provider == "gemini")
    assert ring.status(gemini)["reason"] == "quota atteint"
    assert ring.available()[0].provider == "groq"  # la clé en panne est mise de côté

    # Plus aucune clé disponible : message clair
    await ring.mark_failed(ring.available()[0], 3600, "quota atteint")
    with pytest.raises(gen_mod.StoryError, match="indisponibles"):
        await gen.generate(language="fr", seconds_per_part=6)


async def test_env_keys_are_imported_into_the_dashboard(svc):
    svc.settings.gemini_api_key = GEMINI
    ring = LLMKeyRing(svc.settings)
    await ring.load()
    svc.settings.gemini_api_key = ""  # retirée du .env : toujours là
    ring2 = LLMKeyRing(svc.settings)
    await ring2.load()
    assert any(k.key == GEMINI and k.source == "bot" for k in ring2.all())


async def test_add_keys_from_the_bot(svc, monkeypatch):
    ctx, bot = make_ctx(svc)
    q = await click(ctx, "ak")
    assert "Clés API" in q.edits[-1] and "ElevenLabs" in q.edits[-1]
    checked = []

    async def ping_key(key: LLMKey):
        checked.append(key.provider)
        if "bad" in key.key:
            raise gen_mod.KeyFailure("Clé API invalide", 1, "invalide")
        return f"{key.label} · modèle"

    monkeypatch.setattr(svc.pipeline.stories, "ping_key", ping_key)

    async def sub(api_key=None):
        return {"character_count": 100, "character_limit": 10000}

    monkeypatch.setattr(svc.pipeline.tts, "subscription", sub)

    await click(ctx, "aka")
    m = await type_text(ctx, GEMINI)
    assert m.deleted and any("Clé Google Gemini ajoutée" in t for t in bot.sent)
    await click(ctx, "aka")
    m = await type_text(ctx, "AIzaSy-bad-key-cccccccccccccccccccc")
    assert "refusée" in m.replies[-1]
    m = await type_text(ctx, "nimporte-quoi-dddddddddddddddddddd")
    assert "non reconnue" in m.replies[-1]
    await type_text(ctx, GROQ)
    await click(ctx, "aka")
    await type_text(ctx, "sk_" + "e" * 48)
    assert any("Clé ElevenLabs ajoutée : 9900" in t for t in bot.sent)
    assert any(k.key == "sk_" + "e" * 48 for k in svc.pipeline.tts.keys.all())

    ring = svc.pipeline.llm_keys
    bot_keys = [k for k in ring.all() if k.source == "bot"]
    groq = next(k for k in bot_keys if k.provider == "groq")
    await click(ctx, f"aku:{groq.kid}")
    order = [k.provider for k in ring.all() if k.source == "bot"]
    assert order.index("groq") < order.index("gemini")
    await click(ctx, f"akd:{groq.kid}")
    assert all(k.provider != "groq" for k in ring.all())
    q = await click(ctx, "set")
    assert "Clés d'IA" in q.edits[-1]
