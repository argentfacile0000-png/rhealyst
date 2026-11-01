"""Construction de l'application Telegram."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import BotCommand, MenuButtonWebApp, WebAppInfo
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application

from .handlers import register
from .notifier import TelegramNotifier
from .ui import kb

if TYPE_CHECKING:
    from ..core import Services

log = logging.getLogger(__name__)

COMMANDS = [
    ("start", "Dashboard"),
    ("channels", "Chaînes"),
    ("generate", "Générer maintenant"),
    ("queue", "File d'attente et planning"),
    ("stats", "Statistiques"),
    ("bank", "Banque vidéo"),
    ("new", "Nouvelle chaîne"),
    ("settings", "Réglages système"),
    ("log", "Journal"),
    ("help", "Aide"),
]


def build_application(svc: Services) -> Application:
    s = svc.settings
    if not s.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant dans le .env")
    app = (
        Application.builder()
        .token(s.telegram_bot_token)
        .concurrent_updates(True)
        .connect_timeout(20)
        .read_timeout(30)
        .write_timeout(180)
        .build()
    )
    notifier = TelegramNotifier(app.bot, svc)
    app.bot_data["svc"] = svc
    app.bot_data["notifier"] = notifier
    svc.set_notifier(notifier)
    register(app)
    return app


async def announce(app: Application, svc: Services) -> None:
    try:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMANDS])
    except TelegramError as exc:
        log.warning("set_my_commands: %s", exc)
    svc.bot_username = app.bot.username or ""
    url = svc.settings.webapp_url
    for admin in svc.settings.admin_ids:
        try:
            if url:
                await app.bot.set_chat_menu_button(
                    chat_id=admin, menu_button=MenuButtonWebApp(text="Dashboard", web_app=WebAppInfo(url=url))
                )
            await app.bot.send_message(
                admin, "<b>StoryForge</b> est en ligne.", parse_mode=ParseMode.HTML,
                disable_notification=True, reply_markup=kb([[("Ouvrir le dashboard", "h")]]),
            )
        except TelegramError as exc:
            log.warning("Annonce impossible pour %s : %s (as-tu lancé /start avec ce compte ?)", admin, exc)
