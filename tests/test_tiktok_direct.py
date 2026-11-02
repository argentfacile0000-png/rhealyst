"""Publication directe TikTok conforme : réglages choisis par le créateur, puis publication automatique."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from storyforge.models import VideoStatus
from storyforge.publishers import PublisherError, PublishRequest, tiktok
from storyforge.webapp.server import create_app

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg
PUBLIC = "https://studio.example.com"
CREATOR = {
    "creator_nickname": "Histoires Folles", "creator_username": "histoiresfolles",
    "creator_avatar_url": "https://p16.tiktokcdn.com/avatar.jpeg",
    "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
    "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False, "max_video_post_duration_sec": 600,
}
SETTINGS = {"privacy": "PUBLIC_TO_EVERYONE", "comment": True, "duet": True, "stitch": False, "disclose": False,
            "brand_organic": False, "brand_content": False, "consented_at": "2026-11-02T10:00:00"}


def test_post_info_follows_the_creator_choices():
    info = tiktok.post_info_from(SETTINGS, CREATOR, "Légende")
    assert info["privacy_level"] == "PUBLIC_TO_EVERYONE" and info["is_aigc"] is True
    assert info["disable_comment"] is False and info["disable_stitch"] is True
    assert info["disable_duet"] is True  # désactivé dans le compte : jamais autorisé
    assert info["brand_content_toggle"] is False and info["brand_organic_toggle"] is False
    with pytest.raises(PublisherError, match="réglages de publication"):
        tiktok.post_info_from(None, CREATOR, "x")
    with pytest.raises(PublisherError, match="visibilité"):
        tiktok.post_info_from({**SETTINGS, "privacy": "FOLLOWER_OF_CREATOR"}, CREATOR, "x")
    branded_private = {**SETTINGS, "privacy": "SELF_ONLY", "disclose": True, "brand_content": True}
    assert tiktok.check_post_settings(branded_private, CREATOR)
    assert tiktok.check_post_settings({**SETTINGS, "disclose": True}, CREATOR)  # divulgation sans précision
    branded = tiktok.post_info_from({**SETTINGS, "disclose": True, "brand_content": True}, CREATOR, "x")
    assert branded["brand_content_toggle"] is True


def fake_tiktok(monkeypatch, calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}") if request.method == "POST" else None
        calls.append((path, body))
        ok = {"code": "ok"}
        if path.endswith("/creator_info/query/"):
            return httpx.Response(200, json={"data": CREATOR, "error": ok})
        if path.endswith("/video/init/"):
            return httpx.Response(200, json={"data": {"publish_id": "p1", "upload_url": "https://upload.tiktok/u"},
                                             "error": ok})
        if request.method == "PUT":
            return httpx.Response(201)
        if path.endswith("/status/fetch/"):
            inbox = any(p.endswith("/inbox/video/init/") for p, _ in calls)
            status = {"status": "SEND_TO_USER_INBOX"} if inbox else {"status": "PUBLISH_COMPLETE",
                                                                     "publicaly_available_post_id": [7311]}
            return httpx.Response(200, json={"data": status, "error": ok})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    real = httpx.AsyncClient
    monkeypatch.setattr(tiktok.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def no_sleep(_):
        return None

    monkeypatch.setattr(tiktok, "asyncio", SimpleNamespace(sleep=no_sleep))


async def test_publish_uses_settings_or_falls_back_to_draft(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 2048)
    calls: list = []
    fake_tiktok(monkeypatch, calls)
    creds = {"access_token": "t", "scope": "user.info.basic,video.publish,video.upload"}
    req = PublishRequest(video_path=str(video), title="t", description="", tags=[], caption="Légende", language="fr",
                         privacy="", mode="direct", duration=60, tiktok=SETTINGS)
    res = await tiktok.publish(creds, req, username="histoiresfolles")
    assert res.status == "published" and res.url.endswith("/video/7311")
    init = next(b for p, b in calls if p.endswith("/publish/video/init/"))
    assert init["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE" and init["post_info"]["disable_stitch"] is True

    # Réglages jamais choisis : brouillon, jamais de publication directe
    calls.clear()
    req2 = PublishRequest(video_path=str(video), title="t", description="", tags=[], caption="c", language="fr",
                          privacy="", mode="direct", duration=60, tiktok=None)
    res = await tiktok.publish(creds, req2)
    assert res.status == "draft" and any(p.endswith("/inbox/video/init/") for p, _ in calls)
    assert not any(p.endswith("/publish/video/init/") and "inbox" not in p for p, _ in calls)

    # Vidéo plus longue que ce que le compte autorise
    req3 = PublishRequest(video_path=str(video), title="t", description="", tags=[], caption="c", language="fr",
                          privacy="", mode="direct", duration=900, tiktok=SETTINGS)
    with pytest.raises(PublisherError, match="trop longue"):
        await tiktok.publish(creds, req3)


async def test_settings_page_and_automatic_posting(svc, monkeypatch):
    svc.settings.webapp_enabled = True
    svc.settings.public_url = PUBLIC
    svc.settings.tiktok_client_key, svc.settings.tiktok_client_secret = "ck12345678", "cs"
    ch = await svc.create_channel("Auto TikTok", approval_required=True, min_seconds=5, max_seconds=6)
    await svc.update_channel(ch.id, tiktok_credentials={"access_token": "t", "expires_at": 4102444800,
                                                        "scope": "user.info.basic,video.publish,video.upload"},
                             tiktok_account="histoiresfolles")
    [vid] = await svc.pipeline.generate(ch.id)
    calls: list = []
    fake_tiktok(monkeypatch, calls)
    client = TestClient(create_app(svc))
    token = svc.portal_token(ch.id)

    portal = client.get(f"/connect/{token}").text
    assert f"/connect/{token}/tiktok/settings" in portal and "à choisir" in portal
    page = client.get(f"/connect/{token}/tiktok/settings")
    html = page.text
    assert page.status_code == 200 and "Histoires Folles" in html and "@histoiresfolles" in html
    assert '<option value="" disabled selected>' in html  # aucune visibilité par défaut
    assert 'name="duet" value="1" class="mt-1 w-4 h-4" disabled' in html  # désactivé dans le compte TikTok
    assert "Music Usage Confirmation" in html and f"/media/public/{vid}/" in html and "/static/tiktok.js" in html
    assert "img-src https:" in page.headers["content-security-policy"]

    # Sans visibilité : refus, rien d'enregistré
    r = client.post(f"/connect/{token}/tiktok/settings", data={"action": "save", "comment": "1"})
    assert r.status_code == 400 and "Choisis la visibilité" in r.text
    assert (await svc.get_channel(ch.id)).tiktok_post is None
    # Contenu de marque en privé : refus
    r = client.post(f"/connect/{token}/tiktok/settings",
                    data={"privacy": "SELF_ONLY", "disclose": "1", "brand_content": "1", "action": "save"})
    assert r.status_code == 400 and "ne peut pas être privé" in r.text

    # Réglages + publication automatique, puis « Publier maintenant »
    published = []
    monkeypatch.setattr(svc, "enqueue_publish", lambda v, c=None, tiktok_mode=None: published.append((v, tiktok_mode)))
    r = client.post(f"/connect/{token}/tiktok/settings", follow_redirects=False,
                    data={"privacy": "PUBLIC_TO_EVERYONE", "comment": "1", "auto": "1", "action": "publish",
                          "vid": str(vid)})
    assert r.status_code == 303 and r.headers["location"].endswith("ok=published")
    saved = await svc.get_channel(ch.id)
    assert saved.tiktok_mode == "direct" and saved.tiktok_post["privacy"] == "PUBLIC_TO_EVERYONE"
    assert saved.tiktok_post["comment"] is True and saved.tiktok_post["duet"] is False and saved.tiktok_post["consented_at"]
    assert published == [(vid, "direct")] and (await svc.get_video(vid)).status == VideoStatus.READY
    assert "quelques minutes" in client.get(r.headers["location"]).text

    # La publication automatique (créneau) applique ces réglages
    monkeypatch.undo()
    fake_tiktok(monkeypatch, calls)
    calls.clear()
    await svc.pipeline.publish(vid)
    init = next(b for p, b in calls if p.endswith("/publish/video/init/"))
    assert init["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE" and init["post_info"]["disable_comment"] is False
    assert (await svc.get_video(vid)).status == VideoStatus.PUBLISHED

    # Décocher la publication automatique : brouillons
    r = client.post(f"/connect/{token}/tiktok/settings", follow_redirects=False,
                    data={"privacy": "SELF_ONLY", "action": "save"})
    assert r.status_code == 303 and (await svc.get_channel(ch.id)).tiktok_mode == "draft"
