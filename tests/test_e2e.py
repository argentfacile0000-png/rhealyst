"""Pipeline complet avec OpenAI / ElevenLabs / plateformes simulés (ffmpeg réel)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from storyforge import db
from storyforge.bot import screens
from storyforge.models import Publication, PubStatus, Story, Video, VideoStatus
from storyforge.publishers import PublishResult, tiktok, youtube
from storyforge.stats import overview
from storyforge.webapp.server import create_app

from .conftest import needs_ffmpeg
from .test_units import _init_data

pytestmark = needs_ffmpeg


async def test_generate_render_and_publish(svc, monkeypatch):
    ch = await svc.create_channel("Histoires Folles", parts=2, approval_required=True, bg_categories=["cooking"],
                                  hook_card=True, min_seconds=8, max_seconds=10)
    ids = await svc.pipeline.generate(ch.id)
    assert len(ids) == 2

    async with db.session() as s:
        videos = [await s.get(Video, i) for i in ids]
        story = (await s.execute(select(Story))).scalar_one()
    assert story.title.startswith("Mon coloc")
    for v in videos:
        assert v.status == VideoStatus.PENDING_APPROVAL
        assert Path(v.file_path).exists() and Path(v.thumb_path).exists()
        assert v.bg_file.endswith("long.mp4")
        assert 0 <= v.bg_start and v.bg_start + v.duration <= 40.01
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                            videos[0].file_path], capture_output=True, text=True).stdout
    assert "540,960" in probe

    # Validation puis publication sur les deux plateformes (API simulées)
    await svc.approve(ids[0])
    assert (await svc.next_ready_video(ch.id)).id == ids[0]
    await svc.update_channel(ch.id, youtube_credentials={"token": "x"}, youtube_account="Ma chaîne",
                             tiktok_credentials={"access_token": "t", "expires_at": 9999999999},
                             tiktok_account="histoires")

    async def fake_yt(info, req):
        assert "Partie 1" in req.title and req.privacy == "public"
        return PublishResult(status="published", remote_id="abc", url="https://youtube.com/shorts/abc")

    async def fake_tt(creds, req, username=""):
        assert req.caption.startswith("Partie 1")
        raise tiktok.PublisherError("TikTok : quota")

    monkeypatch.setattr(youtube, "upload", fake_yt)
    monkeypatch.setattr(tiktok, "publish", fake_tt)
    await svc.pipeline.publish(ids[0])
    async with db.session() as s:
        v = await s.get(Video, ids[0])
        pubs = {p.platform: p for p in (await s.execute(select(Publication))).scalars()}
    assert v.status == VideoStatus.PUBLISHED
    assert pubs["youtube"].status == PubStatus.PUBLISHED and pubs["youtube"].url.endswith("abc")
    assert pubs["tiktok"].status == PubStatus.FAILED and "quota" in pubs["tiktok"].error

    # Republier ne renvoie que la plateforme en échec
    async def fake_tt_ok(creds, req, username=""):
        return PublishResult(status="draft", remote_id="p1")

    monkeypatch.setattr(tiktok, "publish", fake_tt_ok)
    monkeypatch.setattr(youtube, "upload", lambda *a, **k: (_ for _ in ()).throw(AssertionError("pas de double upload")))
    await svc.pipeline.publish(ids[0])
    async with db.session() as s:
        pubs = {p.platform: p for p in (await s.execute(select(Publication))).scalars()}
    assert pubs["tiktok"].status == PubStatus.DRAFT
    assert pubs["youtube"].status == PubStatus.PUBLISHED and pubs["youtube"].attempts == 1

    # Dashboards
    o = await overview(svc)
    assert o["today"]["published"] == 2 and o["channels"][0]["pending"] == 1
    for screen in (
        await screens.home(svc), await screens.channel_view(svc, ch.id), await screens.queue(svc),
        await screens.stats(svc), await screens.bank(svc), await screens.channel_videos(svc, ch.id),
        await screens.video_view(svc, ids[0], with_text=True), await screens.channel_group(svc, ch.id, "video"),
        await screens.field_choices(svc, ch.id, "formats"), await screens.events(svc),
        await screens.settings_screen(svc),
    ):
        text, markup = screen
        assert text and len(text) < 4096 and markup.inline_keyboard

    # Mini App
    client = TestClient(create_app(svc))
    assert client.get("/api/overview").status_code == 401
    headers = {"X-Telegram-Init-Data": _init_data(svc.settings.telegram_bot_token, 42)}
    r = client.get("/api/overview", headers=headers)
    assert r.status_code == 200 and r.json()["approvals"][0]["id"] == ids[1]
    assert client.get(f"/media/thumb/{ids[0]}", params={"tma": headers["X-Telegram-Init-Data"]}).status_code == 200
    assert client.post(f"/api/videos/{ids[1]}/discard", headers=headers).json() == {"ok": True}
    assert (await svc.get_video(ids[1])).status == VideoStatus.DISCARDED
    other = {"X-Telegram-Init-Data": _init_data(svc.settings.telegram_bot_token, 7)}
    assert client.get("/api/overview", headers=other).status_code == 401


async def test_slot_and_refill(svc):
    ch = await svc.create_channel("Auto", approval_required=False, active=True, schedule_times=["10:00"],
                                  min_seconds=5, max_seconds=6)
    svc.scheduler.sync_channel(ch)
    assert svc.scheduler.next_run(ch.id) is not None
    await svc.refill()
    assert svc.jobs.count(kind="generate", channel_id=ch.id) == 1
    await svc.jobs._queues["generate"].join()
    assert await svc.buffer_count(ch.id) >= 1
    v = await svc.next_ready_video(ch.id)
    assert v is not None and v.status == VideoStatus.READY
    # Aucune plateforme liée : la vidéo reste prête
    await svc.publish_slot(ch.id)
    await svc.jobs._queues["publish"].join()
    assert (await svc.get_video(v.id)).status == VideoStatus.READY
    # Pause globale : les créneaux ne font rien
    await svc.set_paused(True)
    await svc.refill()
    assert svc.jobs.count() == 0


async def test_short_background_is_looped(svc):
    ch = await svc.create_channel("Loop", bg_categories=["minecraft"], min_seconds=5, max_seconds=6,
                                  approval_required=False)
    [vid] = await svc.pipeline.generate(ch.id)
    v = await svc.get_video(vid)
    assert v.status == VideoStatus.READY and v.bg_file.endswith("short.mp4") and v.duration > 3
