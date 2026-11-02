"""Simulation des interactions Telegram (boutons + saisies) sans réseau."""

from __future__ import annotations

import itertools
import json
from types import SimpleNamespace

from sqlalchemy import select

from storyforge import db
from storyforge.bot import handlers
from storyforge.bot.fields import FIELDS
from storyforge.bot.notifier import TelegramNotifier
from storyforge.models import Video, VideoStatus

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg
_ids = itertools.count(100)


class FakeBot:
    def __init__(self):
        self.sent: list[str] = []
        self.edited: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return SimpleNamespace(message_id=next(_ids), edit_text=self._noop)

    async def send_video(self, chat_id, video=None, caption="", **kw):
        self.sent.append(caption)
        return SimpleNamespace(message_id=next(_ids))

    async def edit_message_text(self, text, **kw):
        self.edited.append(text)

    async def edit_message_caption(self, **kw):
        self.edited.append(kw.get("caption", ""))

    async def _noop(self, *a, **kw):
        return None


class FakeQuery:
    def __init__(self, data: str, media: bool = False):
        self.data = data
        self.message = SimpleNamespace(text=None if media else "écran", chat_id=42, message_id=1)
        self.answers: list = []
        self.edits: list[str] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)

    async def edit_message_caption(self, **kw):
        self.edits.append(kw.get("caption"))


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []
        self.video = self.document = self.audio = self.voice = None
        self.deleted = False

    async def delete(self):
        self.deleted = True

    async def reply_html(self, text, **kw):
        self.replies.append(text)

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return SimpleNamespace(edit_text=self._edit)

    async def _edit(self, text, **kw):
        self.replies.append(text)


def make_ctx(svc):
    bot = FakeBot()
    notifier = TelegramNotifier(bot, svc)
    svc.set_notifier(notifier)
    app = SimpleNamespace(bot_data={"svc": svc, "notifier": notifier})
    return SimpleNamespace(application=app, user_data={}, bot=bot), bot


async def click(ctx, data: str, media: bool = False) -> FakeQuery:
    q = FakeQuery(data, media)
    upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=42),
                          effective_chat=SimpleNamespace(id=42), effective_message=q.message)
    await handlers.on_callback(upd, ctx)
    assert len(q.answers) == 1, f"{data}: answer() appelé {len(q.answers)} fois"
    return q


async def type_text(ctx, text: str) -> FakeMessage:
    msg = FakeMessage(text)
    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=42),
                          effective_chat=SimpleNamespace(id=42), effective_message=msg)
    await handlers.on_text(upd, ctx)
    return msg


async def test_full_dashboard_navigation(svc, tmp_path):
    ctx, bot = make_ctx(svc)

    async def voices():
        return [{"voice_id": "abcdefghij123", "name": "Nova", "gender": "female", "accent": "fr", "age": "young",
                 "category": "premade"}]

    svc.pipeline.tts.list_voices = voices

    # Création d'une chaîne par le flux texte
    await click(ctx, "new")
    assert ctx.user_data["await"]["kind"] == "new_channel"
    await type_text(ctx, "Histoires Folles")
    [ch] = await svc.list_channels()
    cid = ch.id

    for data in ["h", "cl", "gen", "q", "st", "bk", "set", "ev", "bka", f"cv:{cid}", f"ce:{cid}", f"cvv:{cid}:0",
                 "slv", "slv", "stt", f"cd:{cid}", "noop"]:
        await click(ctx, data)
    for group in ["story", "voice", "video", "plan", "pub"]:
        await click(ctx, f"cE:{cid}:{group}")

    # Tous les réglages : choix, multi, bascules
    for f in FIELDS:
        await click(ctx, f"cf:{cid}:{f.key}")
        if f.kind == "choice":
            await click(ctx, f"co:{cid}:{f.key}:{len(f.options) - 1}")
        elif f.kind == "multi":
            await click(ctx, f"cm:{cid}:{f.key}:0")
        ctx.user_data.pop("await", None)
    ch = await svc.get_channel(cid)
    assert ch.language == "it" and ch.parts == 4 and ch.youtube_privacy == "private"

    # Saisies texte
    await click(ctx, f"cf:{cid}:schedule_times")
    await type_text(ctx, "8h, 12:30, 20:15")
    assert (await svc.get_channel(cid)).schedule_times == ["08:00", "12:30", "20:15"]
    await click(ctx, f"cf:{cid}:timezone")
    msg = await type_text(ctx, "Mars/Olympus")
    assert "Fuseau inconnu" in msg.replies[0]
    await type_text(ctx, "America/New_York")
    assert (await svc.get_channel(cid)).timezone == "America/New_York"

    # Voix
    await click(ctx, f"vp:{cid}:0")
    await click(ctx, f"vsel:{cid}:0")
    assert (await svc.get_channel(cid)).voice_name == "Nova"

    # Liaison YouTube sans fichier OAuth -> écran d'aide ; avec fichier -> URL Google
    q = await click(ctx, f"cy:{cid}")
    assert "Configuration manquante" in q.edits[-1]
    secrets = svc.settings.google_client_secrets_file = tmp_path / "client.json"
    secrets.write_text(json.dumps({"installed": {
        "client_id": "x.apps.googleusercontent.com", "client_secret": "y",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]}}))
    q = await click(ctx, f"cy:{cid}")
    assert ctx.user_data["await"]["kind"] == "oauth"
    msg = await type_text(ctx, "http://localhost:8765/?state=inconnu&code=abc")
    assert any("expiré" in r for r in msg.replies)
    await click(ctx, "x")

    # Activation + génération manuelle avec progression live
    await svc.update_channel(cid, parts=1, min_seconds=5, max_seconds=6, approval_required=True,
                             shorts_enabled=True, long_enabled=False, youtube_enabled=True)
    await click(ctx, f"ct:{cid}")
    await click(ctx, f"cg:{cid}")
    await svc.jobs._queues["generate"].join()
    assert any("Génération" in t for t in bot.sent + bot.edited)
    assert any("prête" in t for t in bot.edited)
    async with db.session() as s:
        vids = list((await s.execute(select(Video).where(Video.channel_id == cid))).scalars())
    target = next(x for x in vids if x.status == VideoStatus.PENDING_APPROVAL)
    assert target.tg_messages  # envoyée aux admins pour validation

    # Actions sur la vidéo depuis le message vidéo (média)
    await click(ctx, f"v:{target.id}", media=True)
    await click(ctx, f"vt:{target.id}")
    await click(ctx, f"vf:{target.id}")
    await click(ctx, f"va:{target.id}", media=True)
    assert (await svc.get_video(target.id)).status == VideoStatus.READY
    await click(ctx, f"vn:{target.id}")  # aucune plateforme -> alerte
    await click(ctx, f"cp:{cid}")
    await click(ctx, f"vx:{target.id}", media=True)
    assert (await svc.get_video(target.id)).status == VideoStatus.DISCARDED

    # Pause globale, suppression
    await click(ctx, "sp")
    assert await svc.is_paused()
    await click(ctx, "sp")
    await click(ctx, f"cD:{cid}")
    assert await svc.list_channels() == []


async def test_non_admin_is_rejected(svc):
    ctx, _ = make_ctx(svc)
    q = FakeQuery("h")
    upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=999),
                          effective_chat=SimpleNamespace(id=999), effective_message=q.message)
    await handlers.on_callback(upd, ctx)
    assert q.answers == ["Accès refusé"] and not q.edits
