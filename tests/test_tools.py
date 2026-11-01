"""Découpage en parties, archivage, publication programmée, clés ElevenLabs de secours."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from storyforge.dates import parse_when
from storyforge.models import VideoStatus
from storyforge.tts import elevenlabs as el
from storyforge.tts.keys import KeyRing
from storyforge.video.split import plan_cuts, words_between

from .conftest import needs_ffmpeg
from .test_bot import click, make_ctx, type_text

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=PARIS)  # un lundi


# ---------------------------------------------------------------- dates
@pytest.mark.parametrize("text, expected", [
    ("demain 18h", datetime(2026, 11, 3, 18, 0)),
    ("18h30", datetime(2026, 11, 2, 18, 30)),
    ("9h", datetime(2026, 11, 3, 9, 0)),  # 9h déjà passé : demain
    ("lundi 9h30", datetime(2026, 11, 9, 9, 30)),
    ("vendredi à 21:15", datetime(2026, 11, 6, 21, 15)),
    ("25/12 18:30", datetime(2026, 12, 25, 18, 30)),
    ("25 décembre 20h", datetime(2026, 12, 25, 20, 0)),
    ("2026-12-25 18:30", datetime(2026, 12, 25, 18, 30)),
    ("01/01 10h", datetime(2027, 1, 1, 10, 0)),
    ("dans 2h", datetime(2026, 11, 2, 12, 0)),
    ("Ce soir 21h", datetime(2026, 11, 2, 21, 0)),
])
def test_parse_when(text, expected):
    assert parse_when(text, "Europe/Paris", now=NOW).replace(tzinfo=None) == expected


@pytest.mark.parametrize("text", ["hier", "demain", "2025-01-01 10:00", "32/13 10h", "n'importe quoi 10h"])
def test_parse_when_rejects(text):
    with pytest.raises(ValueError):
        parse_when(text, "Europe/Paris", now=NOW)


# ---------------------------------------------------------------- découpage
def _words(sentences: list[int], wps: float = 4.0) -> tuple[list[list], float]:
    words, t = [], 0.1
    for n in sentences:
        for i in range(n):
            text = "mot." if i == n - 1 else "mot"
            words.append([text, round(t, 3), round(t + 0.2, 3)])
            t += 1 / wps
    return words, t + 0.3


def test_plan_cuts_balanced_at_sentence_ends():
    words, total = _words([30] * 26)  # ~195 s, phrases de 7,5 s
    segs = plan_cuts(words, total, 90)
    assert len(segs) == 3 and segs[0][0] == 0 and segs[-1][1] == total
    sentence_cuts = {round((words[i][2] + words[i + 1][1]) / 2, 3) for i in range(len(words) - 1)
                     if words[i][0].endswith(".")}
    for (a, b) in segs:
        assert b - a <= 90
    for (_, b) in segs[:-1]:
        assert round(b, 3) in sentence_cuts
    lengths = [b - a for a, b in segs]
    assert max(lengths) - min(lengths) < 15  # parties équilibrées

    part = words_between(words, *segs[1])
    assert part and part[0][1] >= 0


def test_plan_cuts_without_words_and_short_video():
    assert plan_cuts(None, 80, 90) == [(0.0, 80)]
    segs = plan_cuts(None, 200, 90)
    assert len(segs) == 3 and all(b - a <= 90 for a, b in segs)


@needs_ffmpeg
async def test_auto_split_and_manual_split(svc):
    ch = await svc.create_channel("Découpe", split_seconds=5, approval_required=True, min_seconds=5, max_seconds=6)
    ids = await svc.pipeline.generate(ch.id)
    assert len(ids) >= 2
    videos = [await svc.get_video(i) for i in ids]
    assert all(v.part_count == len(ids) and v.duration <= 5.5 for v in videos)
    assert [v.part_index for v in videos] == list(range(1, len(ids) + 1))
    assert all(v.status == VideoStatus.PENDING_APPROVAL for v in videos)
    original = await svc.get_video(min(ids) - 1)
    assert original.status == VideoStatus.ARCHIVED and "Découpée en" in original.note

    # Découpage manuel depuis le bot (vidéo entière d'une autre chaîne)
    ch2 = await svc.create_channel("Manuel", approval_required=False, min_seconds=5, max_seconds=6)
    [vid] = await svc.pipeline.generate(ch2.id)
    ctx, bot = make_ctx(svc)
    q = await click(ctx, f"vsp:{vid}")
    assert "ne peut pas" in q.edits[-1]  # le menu propose 60/90/120 s : vidéo de test trop courte
    parts = await svc.split(vid, 4)
    assert len(parts) >= 3 and (await svc.get_video(vid)).status == VideoStatus.ARCHIVED
    statuses = [(await svc.get_video(p)).status for p in parts]
    assert statuses == [VideoStatus.READY] * len(parts)
    with pytest.raises(ValueError):
        await svc.split(parts[0], 2)  # déjà une partie de série


# ---------------------------------------------------------------- archivage & programmation
@needs_ffmpeg
async def test_archive_and_schedule_flow(svc, monkeypatch):
    ch = await svc.create_channel("Planning", approval_required=True, min_seconds=5, max_seconds=6)
    [vid] = await svc.pipeline.generate(ch.id)
    assert await svc.buffer_count(ch.id) == 1
    ctx, _ = make_ctx(svc)

    await click(ctx, f"var:{vid}")
    assert (await svc.get_video(vid)).status == VideoStatus.ARCHIVED
    assert await svc.buffer_count(ch.id) == 0  # une vidéo archivée ne compte plus dans la réserve
    q = await click(ctx, f"cva:{ch.id}:0")
    assert f"#{vid}" in q.edits[-1]
    await click(ctx, f"vua:{vid}")
    assert (await svc.get_video(vid)).status == VideoStatus.PENDING_APPROVAL

    # Programmation par texte libre
    await click(ctx, f"vsc:{vid}")
    assert ctx.user_data["await"] == {"kind": "schedule", "vid": vid}
    await type_text(ctx, "demain 18h")
    v = await svc.get_video(vid)
    assert v.status == VideoStatus.SCHEDULED and v.scheduled_at
    local = v.scheduled_at.replace(tzinfo=timezone.utc).astimezone(PARIS)
    assert (local.hour, local.minute) == (18, 0)
    assert svc.scheduler.sched.get_job(f"pub:{vid}") is not None
    assert await svc.next_ready_video(ch.id) is None
    q = await click(ctx, "q")
    assert "Programmées" in q.edits[-1]

    # Annulation puis bouton rapide
    await click(ctx, f"vsu:{vid}")
    assert (await svc.get_video(vid)).status == VideoStatus.READY and not svc.scheduler.sched.get_job(f"pub:{vid}")
    await click(ctx, f"vsq:{vid}:d12")
    assert (await svc.get_video(vid)).status == VideoStatus.SCHEDULED

    # À l'heure dite : la vidéo part en publication
    published = []
    monkeypatch.setattr(svc, "enqueue_publish", lambda video_id, channel_id=None: published.append(video_id))
    await svc.publish_scheduled(vid)
    assert published == [vid] and (await svc.get_video(vid)).status == VideoStatus.READY

    # Date déjà passée au redémarrage : rattrapée immédiatement
    await svc.schedule(vid, datetime.now(PARIS) + timedelta(hours=1))
    published.clear()
    svc.scheduler.schedule_video(vid, datetime.now(timezone.utc) - timedelta(minutes=5))
    import asyncio

    await asyncio.sleep(0.05)
    assert published == [vid]


# ---------------------------------------------------------------- clés ElevenLabs
@needs_ffmpeg
async def test_elevenlabs_key_failover(svc, monkeypatch):
    svc.settings.elevenlabs_api_key = "sk_principale_aaaaaaaaaaaaaaaaaaaa"
    ring = KeyRing(svc.settings)
    await ring.load()
    await ring.add("sk_secours_bbbbbbbbbbbbbbbbbbbbbbbbb")
    reset = time.time() + 10 * 86400
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["xi-api-key"]
        seen.append((key[:6], request.url.path))
        if request.url.path.endswith("/subscription"):
            return httpx.Response(200, json={"character_count": 100, "character_limit": 10000, "tier": "starter",
                                             "next_character_count_reset_unix": reset})
        if key.startswith("sk_pri"):
            return httpx.Response(401, json={"detail": {"status": "quota_exceeded", "message": "quota"}})
        return httpx.Response(200, json={"audio_base64": "", "alignment": {}})

    real = httpx.AsyncClient
    monkeypatch.setattr(el.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    events = []

    async def on_event(text):
        events.append(text)

    client = el.ElevenLabsClient(model_id="eleven_multilingual_v2", keys=ring)
    client.on_event = on_event
    data = await client._tts_chunk("Bonjour", "voice", el.VoiceSettings(), "", "")
    assert data == {"audio_base64": "", "alignment": {}}
    principal = ring.all()[0]
    assert ring.status(principal)["reason"] == "quota_exceeded" and ring.status(principal)["until"] == int(reset)
    assert ring.current == ring.all()[1].kid
    assert events and "passage à la clé" in events[0]

    # La clé épuisée n'est plus essayée
    seen.clear()
    await client._tts_chunk("Encore", "voice", el.VoiceSettings(), "", "")
    assert all(k == "sk_sec" for k, _ in seen)

    # Plus aucune clé : message clair
    await ring.mark_exhausted(ring.all()[1], time.time() + 3600, "quota_exceeded")
    with pytest.raises(el.ElevenLabsError, match="Toutes les clés"):
        await client._tts_chunk("Fin", "voice", el.VoiceSettings(), "", "")

    # Réactivation manuelle
    await ring.reactivate(principal.kid)
    assert principal in ring.available()


@needs_ffmpeg
async def test_bot_elevenlabs_keys_screen(svc, monkeypatch):
    ctx, bot = make_ctx(svc)

    async def fake_sub(api_key=None):
        if api_key and api_key.startswith("sk_bad"):
            raise el.ElevenLabsError("Clé API ElevenLabs invalide", "invalid_api_key")
        return {"character_count": 500, "character_limit": 30000, "tier": "starter"}

    monkeypatch.setattr(svc.pipeline.tts, "subscription", fake_sub)
    q = await click(ctx, "ek")
    assert "Clés ElevenLabs" in q.edits[-1] and ".env" in q.edits[-1]
    await click(ctx, "eka")
    msg = await type_text(ctx, "sk_bad_cccccccccccccccccccccccccc")
    assert any("refusée" in r for r in msg.replies)
    await type_text(ctx, "sk_nouvelle_dddddddddddddddddddddd")
    keys = svc.pipeline.tts.keys.all()
    assert [k.source for k in keys] == ["env", "bot"]
    assert any("Clé ajoutée" in t for t in bot.sent)
    await click(ctx, f"ekd:{keys[1].kid}")
    assert len(svc.pipeline.tts.keys.all()) == 1
    assert svc.missing_prereqs() == []
