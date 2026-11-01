"""Page de connexion des comptes YouTube / TikTok (OAuth)."""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from storyforge.publishers import tiktok, youtube
from storyforge.webapp.server import create_app

from .conftest import needs_ffmpeg
from .test_bot import click, make_ctx
from .test_units import _init_data

pytestmark = needs_ffmpeg
PUBLIC = "https://studio.example.com"


def google_client(path, kind: str = "web") -> None:
    path.write_text(json.dumps({kind: {
        "client_id": "x.apps.googleusercontent.com", "client_secret": "y",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [f"{PUBLIC}/oauth/youtube", "http://localhost:8765/"]}}))


@pytest.fixture
async def portal_svc(svc, tmp_path):
    s = svc.settings
    s.webapp_enabled = True
    s.public_url = PUBLIC
    s.google_client_secrets_file = tmp_path / "google.json"
    google_client(s.google_client_secrets_file)
    s.tiktok_client_key = "ck"
    s.tiktok_client_secret = "cs"
    return svc


def test_portal_token_is_signed_and_expires(svc):
    token = svc.portal_token(7)
    assert svc.verify_portal_token(token)[0] == 7
    cid, exp, sig = token.split(".")
    assert svc.verify_portal_token(f"8.{exp}.{sig}") is None
    assert svc.verify_portal_token("n'importe quoi") is None
    assert svc.verify_portal_token(svc.portal_token(7, ttl=-1)) is None


async def test_full_connection_flow(portal_svc, monkeypatch):
    svc = portal_svc
    ch = await svc.create_channel("Histoires Folles")
    token = svc.portal_token(ch.id)
    client = TestClient(create_app(svc))

    # Page de connexion
    r = client.get(f"/connect/{token}")
    assert r.status_code == 200
    assert "Histoires Folles" in r.text and "Se connecter avec YouTube" in r.text and "Se connecter avec TikTok" in r.text
    assert "La page de retour affiche une erreur" not in r.text  # retour automatique sur le serveur
    assert client.get("/connect/1.1.faux").status_code == 403

    # Départ vers Google : retour direct sur notre serveur
    r = client.get(f"/connect/{token}/youtube", follow_redirects=False)
    assert r.status_code == 303
    google = urlparse(r.headers["location"])
    q = parse_qs(google.query)
    assert google.netloc == "accounts.google.com"
    assert q["redirect_uri"] == [f"{PUBLIC}/oauth/youtube"] and q["access_type"] == ["offline"]

    async def fake_exchange(secrets, redirect, code, verifier):
        assert code == "g-code" and redirect == f"{PUBLIC}/oauth/youtube" and verifier
        return {"token": "t", "refresh_token": "r"}

    async def fake_title(creds):
        return "Ma chaîne YouTube", creds

    monkeypatch.setattr(youtube, "exchange_code", fake_exchange)
    monkeypatch.setattr(youtube, "channel_title", fake_title)
    r = client.get("/oauth/youtube", params={"state": q["state"][0], "code": "g-code"})
    assert r.status_code == 200 and "YouTube connecté" in r.text and f"/connect/{token}" in r.text
    assert (await svc.get_channel(ch.id)).youtube_account == "Ma chaîne YouTube"
    # Un état ne sert qu'une fois
    assert client.get("/oauth/youtube", params={"state": q["state"][0], "code": "g-code"}).status_code == 400

    # TikTok : URI de retour déduite de PUBLIC_URL
    r = client.get(f"/connect/{token}/tiktok", follow_redirects=False)
    tt = urlparse(r.headers["location"])
    tq = parse_qs(tt.query)
    assert tt.netloc == "www.tiktok.com" and tq["redirect_uri"] == [f"{PUBLIC}/oauth/tiktok"]
    assert "video.publish" in tq["scope"][0]

    async def fake_tt_exchange(key, secret, redirect, code):
        return {"access_token": "a", "refresh_token": "b", "expires_at": time.time() + 86400}

    async def fake_user(access_token):
        return {"display_name": "histoires.folles"}

    monkeypatch.setattr(tiktok, "exchange_code", fake_tt_exchange)
    monkeypatch.setattr(tiktok, "user_info", fake_user)
    r = client.get("/oauth/tiktok", params={"state": tq["state"][0], "code": "t-code"})
    assert r.status_code == 200 and "TikTok connecté" in r.text
    ch = await svc.get_channel(ch.id)
    assert ch.platforms() == ["youtube", "tiktok"]

    # Refus de l'utilisateur
    r = client.get("/oauth/tiktok", params={"error": "access_denied", "state": "x"})
    assert r.status_code == 400 and "Autorisation refusée" in r.text

    # Déconnexion
    r = client.post(f"/connect/{token}/tiktok/disconnect", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("off=tiktok")
    assert not (await svc.get_channel(ch.id)).tiktok_linked
    assert "TikTok est déconnecté" in client.get(r.headers["location"]).text

    # Lien depuis la Mini App
    headers = {"X-Telegram-Init-Data": _init_data(svc.settings.telegram_bot_token, 42)}
    url = client.post(f"/api/channels/{ch.id}/connect-link", headers=headers).json()["url"]
    assert url.startswith(f"{PUBLIC}/connect/")


async def test_paste_fallback_with_desktop_client(portal_svc, monkeypatch):
    svc = portal_svc
    google_client(svc.settings.google_client_secrets_file, kind="installed")
    svc.settings.tiktok_client_key = ""
    ch = await svc.create_channel("Bureau")
    token = svc.portal_token(ch.id)
    client = TestClient(create_app(svc))

    r = client.get(f"/connect/{token}")
    assert "La page de retour affiche une erreur" in r.text  # client « Bureau » : retour sur localhost
    assert "À configurer" in r.text and "TIKTOK_CLIENT_KEY" in r.text

    r = client.get(f"/connect/{token}/youtube", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["redirect_uri"] == ["http://localhost:8765/"]

    async def fake_exchange(secrets, redirect, code, verifier):
        return {"token": "t"}

    async def fake_title(creds):
        return "Chaîne Bureau", creds

    monkeypatch.setattr(youtube, "exchange_code", fake_exchange)
    monkeypatch.setattr(youtube, "channel_title", fake_title)
    pasted = f"http://localhost:8765/?state={q['state'][0]}&code=abc&scope=x"
    r = client.post(f"/connect/{token}/youtube/paste", data={"url": pasted}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("done=youtube")
    assert (await svc.get_channel(ch.id)).youtube_account == "Chaîne Bureau"

    r = client.post(f"/connect/{token}/youtube/paste", data={"url": "http://localhost/?rien"}, follow_redirects=False)
    assert "error=" in r.headers["location"]

    r = client.get(f"/connect/{token}/tiktok", follow_redirects=False)
    assert "error=" in r.headers["location"]


async def test_bot_accounts_screen(svc):
    ctx, _ = make_ctx(svc)
    ch = await svc.create_channel("Sans portail")
    q = await click(ctx, f"ca:{ch.id}")
    assert "Comptes" in q.edits[-1] and "colle ici" in q.edits[-1]

    svc.settings.webapp_enabled = True
    svc.settings.public_url = PUBLIC
    q = await click(ctx, f"ca:{ch.id}")
    assert "page de connexion" in q.edits[-1]
    await svc.update_channel(ch.id, tiktok_credentials={"access_token": "a"}, tiktok_account="x")
    await click(ctx, f"cu:{ch.id}:tiktok")
    assert not (await svc.get_channel(ch.id)).tiktok_linked
