"""Sécurité : seul l'administrateur pilote le bot et le dashboard, rien ne fuit, les attaques sont freinées."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop

from storyforge.bot.access import AccessGate
from storyforge.webapp.auth import validate_init_data
from storyforge.webapp.server import FailureGuard, create_app

from .conftest import needs_ffmpeg
from .test_units import _init_data

pytestmark = needs_ffmpeg
ADMIN = 42


# ---------------------------------------------------------------- bot Telegram
class GateBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.left: list[int] = []
        self.answers: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def leave_chat(self, chat_id, **kw):
        self.left.append(chat_id)

    async def answer_callback_query(self, callback_query_id, text=None, **kw):
        self.answers.append(text)


class Alerts:
    def __init__(self):
        self.texts: list[str] = []

    async def notify(self, text, level="info", channel_id=None):
        self.texts.append(text)


def _message(user_id: int, chat_type: str = "private", text: str = "/start", chat_id: int | None = None) -> Message:
    user = User(id=user_id, first_name="Intrus", is_bot=False, username=f"user{user_id}")
    chat = Chat(id=chat_id or user_id, type=chat_type, title="Groupe" if chat_type != "private" else None)
    return Message(message_id=1, date=datetime.now(timezone.utc), chat=chat, from_user=user, text=text)


def _gate_ctx(svc):
    bot = GateBot()
    alerts = Alerts()
    svc.notifier = alerts
    ctx = SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data={"svc": svc}))
    return ctx, bot, alerts


async def _run(gate, update, ctx) -> bool:
    """Vrai si la mise à jour est arrêtée par la barrière."""
    for obj in (update.message, update.callback_query):
        if obj is not None:
            obj.set_bot(ctx.bot)
            if getattr(obj, "message", None) is not None:
                obj.message.set_bot(ctx.bot)
    try:
        await gate(update, ctx)
    except ApplicationHandlerStop:
        return True
    return False


async def test_gate_lets_only_the_admin_through(svc):
    gate = AccessGate()
    ctx, bot, alerts = _gate_ctx(svc)

    assert not await _run(gate, Update(1, message=_message(ADMIN)), ctx)
    assert bot.sent == [] and alerts.texts == []

    # Un inconnu : arrêté, une seule réponse et une seule alerte malgré l'insistance
    for i in range(5):
        assert await _run(gate, Update(10 + i, message=_message(666, text=f"/settings {i}")), ctx)
    assert bot.sent == [(666, "Accès refusé.")]
    assert len(alerts.texts) == 1 and "666" in alerts.texts[0] and "@user666" in alerts.texts[0]

    # Bouton d'un ancien message : refusé
    q = CallbackQuery(id="q1", from_user=User(id=777, first_name="X", is_bot=False), chat_instance="c", data="sp",
                      message=_message(ADMIN))
    assert await _run(gate, Update(20, callback_query=q), ctx)
    assert bot.answers == ["Accès refusé"]


async def test_gate_blocks_groups_even_for_the_admin(svc):
    gate = AccessGate()
    ctx, bot, alerts = _gate_ctx(svc)
    assert await _run(gate, Update(1, message=_message(ADMIN, "supergroup", chat_id=-100123)), ctx)
    assert bot.left == [-100123] and bot.sent == []
    assert await _run(gate, Update(2, message=_message(555, "group", chat_id=-42)), ctx)
    assert bot.left == [-100123, -42] and len(alerts.texts) == 1


async def test_gate_setup_mode_shows_the_id(svc):
    svc.settings.telegram_admin_ids = ""
    gate = AccessGate()
    ctx, bot, alerts = _gate_ctx(svc)
    assert await _run(gate, Update(1, message=_message(ADMIN)), ctx)
    assert "42" in bot.sent[0][1] and "TELEGRAM_ADMIN_IDS" in bot.sent[0][1] and alerts.texts == []


# ---------------------------------------------------------------- Mini App et API
@pytest.fixture
def client(svc):
    svc.settings.public_url = "https://studio.example.com"
    svc.settings.webapp_enabled = True
    return TestClient(create_app(svc))


def _auth(svc, uid=ADMIN, age=0):
    return {"X-Telegram-Init-Data": _init_data(svc.settings.telegram_bot_token, uid, age=age)}


async def test_api_requires_admin_session(svc, client):
    init = _auth(svc)["X-Telegram-Init-Data"]
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", params={"tma": init}).status_code == 401  # plus de session dans l'URL
    assert client.get("/api/overview", headers=_auth(svc, uid=7)).status_code == 401
    assert client.get("/api/overview", headers=_auth(svc, age=7 * 3600)).status_code == 401  # session expirée
    assert client.post("/api/system/pause", params={"tma": init}).status_code == 401
    assert not await svc.is_paused()
    r = client.get("/api/overview", headers=_auth(svc))
    assert r.status_code == 200 and r.json()["media_token"]
    assert client.get("/healthz").json() == {"ok": True}


async def test_media_token_only_reads_previews(svc, client):
    ch = await svc.create_channel("Médias", approval_required=True, min_seconds=5, max_seconds=6)
    [vid] = await svc.pipeline.generate(ch.id)
    init = _auth(svc)["X-Telegram-Init-Data"]
    assert client.get(f"/media/video/{vid}").status_code == 401
    assert client.get(f"/media/video/{vid}", params={"tma": init}).status_code == 401
    token = svc.media_session_token(ADMIN)
    assert token == svc.media_session_token(ADMIN)  # stable : les miniatures restent en cache
    assert client.get(f"/media/thumb/{vid}", params={"mt": token}).status_code == 200
    assert client.get(f"/media/video/{vid}", params={"mt": token}).status_code == 200
    # Le jeton de lecture ne donne accès à aucune action
    assert client.post(f"/api/videos/{vid}/discard", params={"mt": token}).status_code == 401
    assert client.post(f"/api/videos/{vid}/discard", headers={"X-Telegram-Init-Data": token}).status_code == 401
    # Jeton falsifié, expiré, ou d'un compte qui n'est plus administrateur
    uid, exp, sig = token.split(".")
    assert svc.verify_media_session(f"7.{exp}.{sig}") is None
    assert svc.verify_media_session(svc.media_session_token(ADMIN, ttl=-7200)) is None
    assert svc.verify_media_session(svc.media_session_token(7)) is None
    # Un jeton de lien de connexion n'est pas un jeton de lecture (et inversement)
    assert svc.verify_media_session(svc.portal_token(ADMIN)) is None
    assert svc.verify_portal_token(token) is None


def test_security_headers(client):
    app_page = client.get("/app")
    csp = app_page.headers["content-security-policy"]
    assert "script-src 'self' https://telegram.org;" in csp and "unsafe-inline" not in csp.split("script-src")[1]
    assert "connect-src 'self'" in csp and "frame-ancestors https://web.telegram.org" in csp
    assert app_page.headers["strict-transport-security"].startswith("max-age=")
    assert app_page.headers["x-content-type-options"] == "nosniff"
    assert "server" not in app_page.headers or "uvicorn" not in app_page.headers["server"]
    # Aucun script externe hors Telegram, aucun script inline
    body = app_page.text
    assert "cdn.tailwindcss.com" not in body and "<script>" not in body and "onerror=" not in body
    for asset in ("/static/app.js", "/static/tw-config.js", "/static/vendor/tailwindcss-3.4.17.js"):
        assert client.get(asset).status_code == 200
    expired = client.get("/connect/1.2.abc")
    assert expired.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in expired.headers["content-security-policy"]
    assert "cdn.tailwindcss.com" not in expired.text and "<script>" not in expired.text


async def test_portal_flash_is_escaped(svc, client):
    ch = await svc.create_channel("Flash")
    token = svc.portal_token(ch.id)
    page = client.get(f"/connect/{token}", params={"done": "<script>alert(1)</script>"}).text
    assert "<script>alert" not in page
    page = client.get(f"/connect/{token}", params={"error": "<img src=x>"}).text
    assert "<img src=x>" not in page and "&lt;img" in page


async def test_oauth_state_is_bound_to_its_platform(svc):
    ch = await svc.create_channel("OAuth")
    state = svc.oauth.new("tiktok", ch.id, redirect="https://x/oauth/tiktok")
    with pytest.raises(Exception, match="plateforme"):
        await svc.complete_oauth(state, "code", platform="youtube")
    assert svc.oauth.pop(state) is None  # un état n'est utilisable qu'une fois
    with pytest.raises(Exception, match="expiré"):
        await svc.complete_oauth("", "code")


def test_brute_force_is_blocked(client):
    for _ in range(FailureGuard.LIMIT):
        assert client.get("/api/overview", headers={"X-Telegram-Init-Data": "hash=deadbeef"}).status_code == 401
    assert client.get("/api/overview").status_code == 429
    assert client.get("/app").status_code == 429


def test_large_bodies_are_refused(client):
    r = client.post("/api/system/pause", content=b"x" * 70000)
    assert r.status_code == 413


def test_init_data_rejects_future_dates():
    token = "123456:TEST-token"
    assert validate_init_data(_init_data(token, ADMIN, age=-3600), token) is None
    assert validate_init_data(_init_data(token, ADMIN, age=60), token)["id"] == ADMIN


async def test_notification_survives_bad_html(svc):
    from telegram.error import BadRequest

    from storyforge.bot.notifier import TelegramNotifier

    class PickyBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, parse_mode=None, **kw):
            if parse_mode and "<oops" in text:
                raise BadRequest("Can't parse entities: unsupported start tag")
            self.sent.append((parse_mode, text))

    bot = PickyBot()
    await TelegramNotifier(bot, svc).notify("<b>Échec</b>\n\nErreur <oops> & co", "error")
    assert bot.sent == [(None, "Échec\n\nErreur  & co")]
