"""Clés d'IA et ElevenLabs gérées depuis le dashboard, avec bascule automatique."""

from __future__ import annotations

import json

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


# ---------------------------------------------------------------- AWS Bedrock et choix du modèle
AWS = "ABSKQmVkcm9ja0FQSUtleS1hYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejEyMzQ1Njc4OTA="


def bedrock_transport(monkeypatch, calls, deny_model=""):
    from storyforge.story import bedrock

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.host, request.url.path, request.headers.get("authorization", "")))
        if request.url.path.endswith("/converse"):
            body = json.loads(request.content)
            if deny_model and deny_model in request.url.path:
                return httpx.Response(403, json={"message": "You don't have access to the model with the specified model ID."})
            assert body["system"] and body["messages"][0]["role"] == "user"
            return httpx.Response(200, json={"output": {"message": {"content": [{"text": json.dumps(STORY_JSON)}]}},
                                             "usage": {"inputTokens": 12, "outputTokens": 34}})
        if request.url.path == "/inference-profiles":
            return httpx.Response(200, json={"inferenceProfileSummaries": [{"inferenceProfileId": "eu.anthropic.claude-sonnet-5-5"}]})
        if request.url.path == "/foundation-models":
            return httpx.Response(200, json={"modelSummaries": [{"modelId": "anthropic.claude-sonnet-5-5"},
                                                                {"modelId": "amazon.nova-pro-v1:0"}]})
        return httpx.Response(404, json={"message": "not found"})

    real = httpx.AsyncClient
    monkeypatch.setattr(bedrock.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_bedrock_key_detection_and_config(settings):
    assert detect_provider(AWS) == "bedrock" and detect_provider("bedrock-api-key-YmVkcm9jay5hbWF6b24=") == "bedrock"
    cfg = LLMKey(AWS, "bedrock", "bot", "amazon.nova-pro-v1:0", "eu-west-3").config(settings)
    assert cfg.base_url == "https://bedrock-runtime.eu-west-3.amazonaws.com" and cfg.model == "amazon.nova-pro-v1:0"
    assert LLMKey(GROQ, "groq", "bot", "openai/gpt-oss-120b").config(settings).model == "openai/gpt-oss-120b"


async def test_story_with_bedrock_and_failover(svc, monkeypatch):
    calls = []
    bedrock_transport(monkeypatch, calls, deny_model="opus")
    svc.settings.openai_api_key = ""
    ring = svc.pipeline.llm_keys
    await db.kv_set("llm_keys", [])
    await ring.load()
    await ring.add(AWS, "bedrock", model="anthropic.claude-opus-5-5", region="eu-west-3")  # modèle non activé
    await ring.add("bedrock-api-key-c2Vjb25kLWtleS1hYmNkZWZnaGlq", "bedrock", model="eu.anthropic.claude-sonnet-5-5",
                   region="eu-west-3")
    gen = svc.pipeline.stories
    gen._client = None
    story = await gen.generate(language="fr", seconds_per_part=6)
    assert story.title and story.model == "eu.anthropic.claude-sonnet-5-5"
    converse = [c for c in calls if c[2].endswith("/converse")]
    assert converse[0][1] == "bedrock-runtime.eu-west-3.amazonaws.com" and converse[0][3] == f"Bearer {AWS}"
    assert "eu.anthropic.claude-sonnet-5-5" in converse[-1][2]
    first = ring.get(LLMKey(AWS, "bedrock", "bot").kid)
    assert ring.status(first)["reason"] == "modèle non activé"


async def test_add_aws_key_and_choose_models_from_the_bot(svc, monkeypatch):
    calls = []
    bedrock_transport(monkeypatch, calls)
    ctx, bot = make_ctx(svc)
    pinged = []

    async def ping_key(key):
        pinged.append((key.provider, key.model))
        return f"{key.label} · {key.model}"

    monkeypatch.setattr(svc.pipeline.stories, "ping_key", ping_key)
    await click(ctx, "aka")
    m = await type_text(ctx, AWS)
    assert m.deleted and ctx.user_data["aws_key"] == AWS
    q = await click(ctx, "akgs::2")  # eu-west-3, Paris
    k = next(x for x in svc.pipeline.llm_keys.all() if x.provider == "bedrock")
    assert k.region == "eu-west-3" and "aws_key" not in ctx.user_data
    models = ctx.user_data["models"][k.kid]
    assert models[0] == "anthropic.claude-sonnet-5-5" and "eu.anthropic.claude-sonnet-5-5" in models
    await click(ctx, f"akms:{k.kid}:{models.index('eu.anthropic.claude-sonnet-5-5')}")
    assert svc.pipeline.llm_keys.get(k.kid).model == "eu.anthropic.claude-sonnet-5-5"
    assert any("Modèle choisi et vérifié" in t for t in bot.sent)

    # Saisie libre d'un modèle, changement de région
    await click(ctx, f"akmo:{k.kid}")
    m = await type_text(ctx, "pas un modèle !")
    assert "invalide" in m.replies[-1]
    await type_text(ctx, "amazon.nova-pro-v1:0")
    assert svc.pipeline.llm_keys.get(k.kid).model == "amazon.nova-pro-v1:0"
    await click(ctx, f"akg:{k.kid}")
    await click(ctx, f"akgo:{k.kid}")
    await type_text(ctx, "us-east-2")
    assert svc.pipeline.llm_keys.get(k.kid).region == "us-east-2"

    # Choix du modèle d'une clé Groq : liste du fournisseur
    async def list_models(key):
        return ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "whisper-large-v3"]

    monkeypatch.setattr(svc.pipeline.stories, "list_models", list_models)
    g = await svc.pipeline.llm_keys.add(GROQ, "groq")
    q = await click(ctx, f"akm:{g.kid}")
    assert "Modèle" in q.edits[-1] and "llama-3.3-70b-versatile" in q.edits[-1]
    await click(ctx, f"akms:{g.kid}:1")
    assert svc.pipeline.llm_keys.get(g.kid).model == "openai/gpt-oss-120b"
    q = await click(ctx, "ak")
    assert "modèle openai/gpt-oss-120b" in q.edits[-1] and "modèle amazon.nova-pro-v1:0, us-east-2" in q.edits[-1]


async def test_custom_short_and_long_durations(svc):
    from storyforge.bot.fields import FIELD_MAP

    ch = await svc.create_channel("Durées")
    ctx, _ = make_ctx(svc)
    custom = len(FIELD_MAP["duration"].options) - 1
    await click(ctx, f"co:{ch.id}:duration:{custom}")
    assert ctx.user_data["await"] == {"kind": "field", "cid": ch.id, "key": "duration"}
    m = await type_text(ctx, "5")
    assert "entre 10 et 1200" in m.replies[-1]
    m = await type_text(ctx, "1300")
    assert "entre 10 et 1200" in m.replies[-1]
    await type_text(ctx, "75 à 40")
    c = await svc.get_channel(ch.id)
    assert (c.min_seconds, c.max_seconds) == (40, 75)
    await click(ctx, f"co:{ch.id}:duration:{custom}")
    await type_text(ctx, "60")
    c = await svc.get_channel(ch.id)
    assert (c.min_seconds, c.max_seconds) == (60, 60) and FIELD_MAP["duration"].value_text(c) == "60 s"
    await click(ctx, f"co:{ch.id}:long_duration:{len(FIELD_MAP['long_duration'].options) - 1}")
    await type_text(ctx, "6-10")
    c = await svc.get_channel(ch.id)
    assert (c.long_min_minutes, c.long_max_minutes) == (6, 10)


async def test_custom_split_duration(svc):
    from storyforge.bot.fields import FIELD_MAP

    ch = await svc.create_channel("Découpe libre")
    ctx, _ = make_ctx(svc)
    custom = len(FIELD_MAP["split_seconds"].options) - 1
    await click(ctx, f"co:{ch.id}:split_seconds:{custom}")
    m = await type_text(ctx, "1500")
    assert "entre 10 et 1200" in m.replies[-1]
    await type_text(ctx, "180")
    assert (await svc.get_channel(ch.id)).split_seconds == 180
    await click(ctx, f"co:{ch.id}:split_seconds:{custom}")
    await type_text(ctx, "0")
    assert (await svc.get_channel(ch.id)).split_seconds == 0
    await svc.update_channel(ch.id, min_seconds=900, max_seconds=1200)
    assert (await svc.get_channel(ch.id)).max_seconds == 1200


async def test_story_system_prompt_from_settings(svc, monkeypatch):
    from types import SimpleNamespace

    from storyforge.story.prompts import SYSTEM_PROMPT

    ctx, bot = make_ctx(svc)
    docs = []

    async def send_document(chat_id, document=None, filename="", **kw):
        docs.append((filename, document.read().decode()))

    monkeypatch.setattr(bot, "send_document", send_document, raising=False)
    q = await click(ctx, "set")
    q = await click(ctx, "sp0")
    assert "Prompt des histoires" in q.edits[-1] and "originale" in q.edits[-1]
    await click(ctx, "spf")
    assert docs and docs[0][0] == "prompt-histoires.txt" and docs[0][1] == SYSTEM_PROMPT

    await click(ctx, "spe")
    m = await type_text(ctx, "trop court")
    assert "trop court" in m.replies[-1].lower()
    custom = "Tu écris des histoires d'horreur courtes et glaçantes. " * 3 + "Réponds en JSON."
    await type_text(ctx, custom)
    assert svc.pipeline.stories.system_prompt == custom.strip()
    q = await click(ctx, "sp0")
    assert "personnalisée" in q.edits[-1]

    # Le prompt personnalisé est bien envoyé à l'IA, et survit à un redémarrage
    sent = []
    real = svc.pipeline.stories._client.chat.completions.create

    async def spy(**kw):
        sent.append(kw["messages"][0]["content"])
        return await real(**kw)

    svc.pipeline.stories._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=spy)))
    svc.pipeline.stories._client.chat.completions.create = spy
    await svc.pipeline.stories.generate(language="fr", seconds_per_part=6)
    assert sent[0] == custom.strip()
    from storyforge import db

    assert await db.kv_get("story_system_prompt") == custom.strip()

    # Fichier .txt, puis retour à l'original
    await click(ctx, "spe")

    class TgFile:
        async def download_as_bytearray(self):
            return bytearray(("Version fichier. " * 10 + "JSON").encode())

    async def get_file():
        return TgFile()

    from storyforge.bot import handlers

    from .test_bot import FakeMessage

    msg = FakeMessage("")
    msg.document = SimpleNamespace(file_name="p.txt", file_size=200, get_file=get_file)
    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=42),
                          effective_chat=SimpleNamespace(id=42), effective_message=msg)
    await handlers.on_file(upd, ctx)
    assert svc.pipeline.stories.system_prompt.startswith("Version fichier.")
    await click(ctx, "spr")
    assert svc.pipeline.stories.system_prompt is None


def test_parse_json_reply_variants():
    from storyforge.story.generator import parse_json_reply

    assert parse_json_reply('```json\n{"title": "A"}\n```') == {"title": "A"}
    assert parse_json_reply('Here is the story:\n{"title": "B", "narration": "Line one.\nLine two."}\nEnjoy!') == {
        "title": "B", "narration": "Line one.\nLine two."}  # retour à la ligne brut dans la chaîne
    assert parse_json_reply("pas du tout du JSON") is None


async def test_story_with_the_viral_master_prompt_format(svc):
    """Format du prompt « Viral storytime engine » : narration, hook, category, tone, estimated_duration_seconds."""
    from types import SimpleNamespace

    narration = ("My sister borrowed my car for one weekend and returned it with a parking ticket from a city she "
                 "swore she never visited. I didn't say anything at first. Then the toll receipts arrived.")
    reply = ('{"title": "The Toll Receipt", "hook": "My sister borrowed my car for one weekend and returned it with a '
             'parking ticket from a city she swore she never visited.", "narration": "' + narration.replace(". ", ".\n")
             + '", "category": "Family Drama", "tone": "suspicious", "estimated_duration_seconds": 12}')
    replies = ["Sorry, here it is: " + reply[:40], reply]  # 1re réponse cassée : nouvel essai automatique
    calls = []

    async def create(**kw):
        calls.append(kw["messages"])
        content = replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20))

    gen = svc.pipeline.stories
    gen._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    story = await gen.generate(language="en", seconds_per_part=12)
    assert story.title == "The Toll Receipt" and story.parts[0].startswith("My sister borrowed my car")
    assert "family" in " ".join(story.hashtags).lower() and story.tiktok_caption.startswith("The Toll Receipt #")
    assert len(calls) == 2 and "cut off" in calls[1][-1]["content"]

    replies[:] = ["nope", "still nope"]
    import pytest as _pytest

    with _pytest.raises(gen_mod.StoryError, match="Début de la réponse : « still nope »"):
        await gen.generate(language="en", seconds_per_part=12)


def test_system_prompt_and_request_ask_for_the_same_json():
    import re as _re

    from storyforge.story.prompts import SYSTEM_PROMPT, build_user_prompt
    from storyforge.story.styles import FORMATS, get_language, random_dna

    user = build_user_prompt(language=get_language("fr"), fmt=next(iter(FORMATS.values())), dna=random_dna("any"),
                             parts=2, words_per_part=150, seconds_per_part=60, avoid_titles=[])
    keys = _re.findall(r'^\s*"(\w+)":', user, flags=_re.M)
    assert keys == ["title", "hook", "parts", "youtube_title", "description", "hashtags", "tiktok_caption",
                    "category", "tone"]
    for k in keys:
        assert k in SYSTEM_PROMPT  # le prompt système annonce exactement les mêmes clés
    assert "estimated_duration_seconds" not in SYSTEM_PROMPT and "American English" not in SYSTEM_PROMPT
    assert "exactly 2 string(s)" in user and "French" in user


async def test_old_viral_prompt_is_replaced_by_the_adapted_default(svc):
    from storyforge.core import Services

    await db.kv_set("story_system_prompt", "VIRAL STORYTIME ENGINE — MASTER SYSTEM PROMPT\n... "
                                           '"estimated_duration_seconds": 180 ...')
    s2 = Services(svc.settings)
    await s2.start()
    try:
        assert s2.pipeline.stories.system_prompt is None and await db.kv_get("story_system_prompt") == ""
    finally:
        await s2.stop()


async def test_broken_and_cut_replies_are_repaired_or_retried(svc):
    from types import SimpleNamespace

    from storyforge.story.generator import parse_json_reply

    # Guillemets de dialogue non échappés : réparés
    bad = ('```json\n{ "title": "Quelle est la pire vengeance accidentelle ?", "parts": [ "Ma sœur a dit '
           '"jamais de la vie" et elle est partie. Trois jours plus tard, tout a changé." ], "hashtags": ["storytime"] }\n```')
    data = parse_json_reply(bad)
    assert data["parts"][0].startswith("Ma sœur a dit") and "jamais de la vie" in data["parts"][0]

    # Réponse coupée (limite de longueur) : nouvel essai, la version complète est gardée
    full = json.dumps({"title": "La facture", "hook": "Mon voisin m'a facturé ma propre haie.",
                       "parts": ["Mon voisin m'a facturé ma propre haie. Et il avait gardé le reçu."],
                       "hashtags": ["storytime"], "category": "voisins", "tone": "ironique"})
    replies = [("```json\n" + full[:70], "length"), (full, "stop")]
    seen = []

    async def create(**kw):
        seen.append(kw["messages"][-1]["content"])
        content, reason = replies.pop(0) if replies else ('{"title": "x", "parts": ["encore', "length")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=reason)],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

    gen = svc.pipeline.stories
    gen._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    story = await gen.generate(language="fr", seconds_per_part=6)
    assert story.title == "La facture" and story.parts[0].endswith("reçu.")
    assert "cut off" in seen[1]

    # Réponse coupée puis vraie suite : les morceaux sont recollés (avec chevauchement retiré)
    replies[:] = [(full[:60], "length"), (full[50:120], "length"), (full[120:], "stop")]
    seen.clear()
    story = await gen.generate(language="fr", seconds_per_part=6)
    assert story.title == "La facture" and story.parts[0].endswith("reçu.")
    assert "Continue EXACTLY" in seen[1]

    # Coupée à chaque fois : message clair
    replies[:] = [("{" + '"title": "x", "parts": ["une histoire', "length"), ('{"title": "x", "parts": ["encore', "length")]
    with pytest.raises(gen_mod.StoryError, match="coupée deux fois"):
        await gen.generate(language="fr", seconds_per_part=6)


def test_mixed_dialogue_quotes_are_normalized():
    from storyforge.story.generator import fix_quotes

    assert fix_quotes('Il a dit «Pour rien." Puis "Oui."') == "Il a dit «Pour rien.» Puis «Oui.»"
