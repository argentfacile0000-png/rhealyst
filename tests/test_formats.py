"""Vidéos longues YouTube, publication Instagram, mises à jour automatiques."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from storyforge import version as version_mod
from storyforge.jobs import JobManager
from storyforge.models import Publication, PubStatus, VideoFormat, VideoStatus
from storyforge.publishers import PublishRequest, PublishResult, instagram, tiktok, youtube
from storyforge.webapp.server import create_app

from .conftest import needs_ffmpeg
from .test_bot import click, make_ctx

pytestmark = needs_ffmpeg
PUBLIC = "https://studio.example.com"


def _dims(path: str) -> str:
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()


async def test_long_form_video_goes_to_youtube_only(svc, monkeypatch):
    ch = await svc.create_channel("Longues", long_enabled=True, long_min_minutes=1, long_max_minutes=1,
                                  approval_required=False, youtube_credentials={"t": 1},
                                  tiktok_credentials={"access_token": "a", "expires_at": 9e12},
                                  instagram_credentials={"access_token": "i", "expires_at": 9e12})
    assert ch.formats_enabled() == [VideoFormat.VERTICAL, VideoFormat.HORIZONTAL]
    prompts = []
    original = svc.pipeline.stories._client.chat.completions.create

    async def spy(**kwargs):
        prompts.append(kwargs)
        return await original(**kwargs)

    svc.pipeline.stories._client.chat.completions.create = spy
    [vid] = await svc.pipeline.generate(ch.id, kind=VideoFormat.HORIZONTAL)
    v = await svc.get_video(vid)
    assert v.format == VideoFormat.HORIZONTAL and v.status == VideoStatus.READY
    assert _dims(v.file_path) == "960,540"  # format paysage (réglages de test 540×960)
    assert v.cover_path and Path(v.cover_path).exists()
    assert "LONG-FORM" in prompts[0]["messages"][1]["content"] and prompts[0]["max_completion_tokens"] == 12000
    assert await svc.next_ready_video(ch.id) is None  # le stock vertical est séparé
    assert (await svc.next_ready_video(ch.id, VideoFormat.HORIZONTAL)).id == vid

    sent: list[PublishRequest] = []

    async def fake_yt(info, req):
        sent.append(req)
        return PublishResult(status="published", remote_id="L1", url="https://youtube.com/watch?v=L1")

    async def boom(*a, **k):
        raise AssertionError("une vidéo longue ne va pas sur TikTok ni Instagram")

    monkeypatch.setattr(youtube, "upload", fake_yt)
    monkeypatch.setattr(tiktok, "publish", boom)
    monkeypatch.setattr(instagram, "publish", boom)
    await svc.pipeline.publish(vid)
    assert sent[0].kind == VideoFormat.HORIZONTAL and sent[0].thumbnail_path == v.cover_path
    assert "#shorts" not in youtube._build_description(sent[0])
    assert (await svc.get_video(vid)).status == VideoStatus.PUBLISHED


async def test_slots_and_refill_per_format(svc):
    ch = await svc.create_channel("Planning", active=True, long_enabled=True, schedule_times=["09:00"],
                                  long_schedule_times=["20:30"], min_seconds=5, max_seconds=6,
                                  long_min_minutes=1, long_max_minutes=1, approval_required=False)
    svc.scheduler.sync_channel(ch)
    ids = sorted(j.id for j in svc.scheduler.sched.get_jobs() if j.id.startswith(f"slot:{ch.id}:"))
    assert ids == [f"slot:{ch.id}:horizontal:20:30", f"slot:{ch.id}:vertical:09:00"]
    assert {k for _, _, k in svc.scheduler.upcoming()} == {"vertical", "horizontal"}
    await svc.refill()
    assert svc.jobs.count(kind="generate", channel_id=ch.id, tag="vertical") == 1
    assert svc.jobs.count(kind="generate", channel_id=ch.id, tag="horizontal") == 1
    await svc.jobs._queues["generate"].join()
    assert await svc.buffer_count(ch.id, VideoFormat.HORIZONTAL) == 1
    # Les Shorts désactivés : plus de créneau vertical
    ch = await svc.update_channel(ch.id, shorts_enabled=False)
    assert all(":vertical:" not in j.id for j in svc.scheduler.sched.get_jobs())


async def test_instagram_publish_with_signed_public_url(svc, monkeypatch):
    ch = await svc.create_channel("Reels", approval_required=False, min_seconds=5, max_seconds=6,
                                  instagram_credentials={"access_token": "tok", "user_id": "17841",
                                                         "expires_at": time.time() + 50 * 86400,
                                                         "obtained_at": time.time()},
                                  instagram_account="@reels")
    [vid] = await svc.pipeline.generate(ch.id)

    # Sans adresse publique : échec explicite
    await svc.pipeline.publish(vid)
    async with svc_session() as s:
        pub = (await s.execute(_pub_query(vid))).scalar_one()
    assert pub.status == PubStatus.FAILED and "PUBLIC_URL" in pub.error

    svc.settings.webapp_enabled = True
    svc.settings.public_url = PUBLIC
    seen = {}

    async def fake_publish(creds, req, version="v26.0"):
        seen["url"], seen["caption"] = req.video_url, req.caption
        return PublishResult(status="published", remote_id="M1", url="https://www.instagram.com/reel/abc/")

    monkeypatch.setattr(instagram, "publish", fake_publish)
    await svc.pipeline.publish(vid)
    assert seen["url"].startswith(f"{PUBLIC}/media/public/{vid}/") and seen["caption"]

    client = TestClient(create_app(svc))
    path = urlparse(seen["url"]).path
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and len(r.content) > 1000
    assert client.get(path.replace(path.split("/")[-1], "0" * 32 + ".mp4")).status_code == 403


def svc_session():
    from storyforge import db

    return db.session()


def _pub_query(vid):
    from sqlalchemy import select

    return select(Publication).where(Publication.video_id == vid, Publication.platform == "instagram")


async def test_instagram_api_flow(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/17841/media"):
            form = parse_qs(request.content.decode())
            assert form["media_type"] == ["REELS"] and form["video_url"] == ["https://x.test/v.mp4"]
            return httpx.Response(200, json={"id": "C1"})
        if path.endswith("/C1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "M9"})
        if path.endswith("/M9"):
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/M9/"})
        return httpx.Response(404, json={"error": {"message": "inconnu", "code": 100}})

    real = httpx.AsyncClient
    monkeypatch.setattr(instagram.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    req = PublishRequest(video_path="", title="", description="", tags=[], caption="Partie 1 #storytime",
                         language="fr", privacy="", video_url="https://x.test/v.mp4")
    res = await instagram.publish({"access_token": "t", "user_id": "17841"}, req, poll_interval=0)
    assert res.remote_id == "M9" and res.url.endswith("/reel/M9/")
    assert [p for _, p in calls][-1].endswith("/M9")

    url = instagram.build_auth_url("app", f"{PUBLIC}/oauth/instagram", "st")
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == [instagram.SCOPES] and q["redirect_uri"] == [f"{PUBLIC}/oauth/instagram"]
    fresh = {"access_token": "t", "expires_at": time.time() + 50 * 86400, "obtained_at": time.time()}
    assert await instagram.refresh_if_needed(fresh) == (fresh, False)
    with pytest.raises(instagram.PublisherError, match="expiré"):
        await instagram.refresh_if_needed({"access_token": "t", "expires_at": time.time() - 10})


async def test_instagram_connection_portal(svc, monkeypatch):
    s = svc.settings
    s.webapp_enabled, s.public_url = True, PUBLIC
    ch = await svc.create_channel("Insta")
    assert "app Instagram" in svc.platform_problem("instagram", ch.id)
    s.instagram_app_id, s.instagram_app_secret = "app", "secret"
    assert svc.platform_problem("instagram", ch.id) is None and svc.oauth_is_automatic("instagram", ch.id)

    token = svc.portal_token(ch.id)
    client = TestClient(create_app(svc))
    assert "Se connecter avec Instagram" in client.get(f"/connect/{token}").text
    r = client.get(f"/connect/{token}/instagram", follow_redirects=False)
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.netloc == "www.instagram.com" and q["redirect_uri"] == [f"{PUBLIC}/oauth/instagram"]

    async def fake_exchange(app_id, secret, redirect, code, version="v26.0"):
        assert code == "ig-code"
        return {"access_token": "long", "user_id": "1", "expires_at": time.time() + 5e6, "obtained_at": time.time()}

    async def fake_me(token, version="v26.0"):
        return {"user_id": "17841", "username": "histoires.folles"}

    monkeypatch.setattr(instagram, "exchange_code", fake_exchange)
    monkeypatch.setattr(instagram, "user_info", fake_me)
    r = client.get("/oauth/instagram", params={"state": q["state"][0], "code": "ig-code"})
    assert r.status_code == 200 and "Instagram connecté" in r.text
    ch = await svc.get_channel(ch.id)
    assert ch.instagram_account == "@histoires.folles" and ch.instagram_credentials["user_id"] == "17841"
    assert "instagram" in ch.platforms() and "instagram" not in ch.platforms(VideoFormat.HORIZONTAL)


async def test_bot_format_chooser_and_instagram(svc):
    ctx, _ = make_ctx(svc)
    ch = await svc.create_channel("Deux formats", long_enabled=True)
    q = await click(ctx, f"cg:{ch.id}")
    assert "Quel format" in q.edits[-1]
    q = await click(ctx, f"ca:{ch.id}")
    assert "Instagram" in q.edits[-1]
    q = await click(ctx, f"cv:{ch.id}")
    assert "Vidéos longues" in q.edits[-1] and "Instagram" in q.edits[-1]
    q = await click(ctx, f"cE:{ch.id}:content")
    assert "Durée des vidéos longues" in q.edits[-1]


async def test_busy_file_tracks_running_jobs(tmp_path):
    busy = tmp_path / "state" / "busy"
    jm = JobManager(1, 1, busy_file=busy)
    await jm.start()
    gate = asyncio.Event()
    seen = {}

    async def work():
        seen["during"] = busy.exists()
        await gate.wait()

    jm.submit("generate", "test", work)
    await asyncio.sleep(0.05)
    gate.set()
    await jm._queues["generate"].join()
    await jm.stop()
    assert seen["during"] is True and not busy.exists()


async def test_version_and_auto_update_toggle(svc, tmp_path, monkeypatch):
    vfile = tmp_path / ".version.json"
    vfile.write_text(json.dumps({"sha": "a" * 40, "message": "Ajout des vidéos longues", "date": "2026-11-02"}))
    monkeypatch.setattr(version_mod, "VERSION_FILE", vfile)
    assert version_mod.deployed_version()["short"] == "aaaaaaa"
    ctx, _ = make_ctx(svc)
    q = await click(ctx, "set")
    assert "aaaaaaa" in q.edits[-1] and "mises à jour auto" in q.edits[-1]
    await click(ctx, "sau")
    assert version_mod.auto_update_flag(svc.settings.data_dir).exists()
    await click(ctx, "sau")
    assert not version_mod.auto_update_flag(svc.settings.data_dir).exists()
