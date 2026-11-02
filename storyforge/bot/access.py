"""Barrière d'accès du bot : seuls les administrateurs, en conversation privée, atteignent le dashboard.

Elle s'exécute avant tous les autres handlers (groupe -1). Une mise à jour refusée s'arrête là :
aucun écran, aucune action, aucune saisie n'est traitée. Les tentatives d'accès sont signalées
aux administrateurs (une fois par heure et par personne) et inscrites au journal.
"""

from __future__ import annotations

import html
import logging
import time
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .. import db

if TYPE_CHECKING:
    from ..core import Services

log = logging.getLogger(__name__)

REPLY_EVERY = 600  # au plus une réponse « Accès refusé » toutes les 10 min par personne
ALERT_EVERY = 3600  # au plus une alerte aux administrateurs par heure et par personne


class AccessGate:
    def __init__(self) -> None:
        self._replied: dict[int, float] = {}
        self._alerted: dict[int, float] = {}

    @staticmethod
    def _due(store: dict[int, float], key: int, every: int) -> bool:
        now = time.monotonic()
        if now - store.get(key, -every) < every:
            return False
        store[key] = now
        if len(store) > 5000:  # borne la mémoire en cas d'afflux
            for k in sorted(store, key=store.get)[:2500]:
                store.pop(k, None)
        return True

    async def __call__(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not isinstance(update, Update):
            raise ApplicationHandlerStop
        svc: Services = context.application.bot_data["svc"]
        admins = svc.settings.admin_ids
        chat = update.effective_chat
        user = update.effective_user

        if chat is not None and chat.type != ChatType.PRIVATE:
            # Le dashboard ne s'utilise qu'en privé : le bot quitte les groupes et canaux où on l'ajoute.
            if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                try:
                    await context.bot.leave_chat(chat.id)
                except TelegramError:
                    pass
                if user is None or user.id not in admins:
                    await self._report(svc, user, f"ajout du bot dans « {getattr(chat, 'title', '') or chat.id} »")
            raise ApplicationHandlerStop

        if user is not None and user.id in admins and not user.is_bot:
            return  # accès autorisé : les handlers suivants s'exécutent

        await self._deny(update, context, admins)
        raise ApplicationHandlerStop

    async def _deny(self, update: Update, context: ContextTypes.DEFAULT_TYPE, admins: set[int]) -> None:
        user = update.effective_user
        if user is None:
            return
        if update.callback_query:
            try:
                await update.callback_query.answer("Accès refusé", show_alert=True)
            except TelegramError:
                pass
        elif update.effective_message and self._due(self._replied, user.id, REPLY_EVERY):
            try:
                if admins:
                    await update.effective_message.reply_text("Accès refusé.")
                else:  # première installation : aide à trouver son identifiant
                    await update.effective_message.reply_html(
                        f"Ton identifiant Telegram est <code>{user.id}</code>.\n"
                        "Ajoute-le dans <code>TELEGRAM_ADMIN_IDS</code> du fichier <code>.env</code> puis redémarre le bot."
                    )
            except TelegramError:
                pass
        if admins:
            await self._report(context.application.bot_data["svc"], user,
                               "bouton du dashboard" if update.callback_query else "message au bot")

    async def _report(self, svc: Services, user, what: str) -> None:
        if user is None or not self._due(self._alerted, user.id, ALERT_EVERY):
            return
        who = f"@{user.username}" if getattr(user, "username", None) else (getattr(user, "full_name", "") or "inconnu")
        log.warning("Accès refusé : %s (id %s), %s", who, user.id, what)
        text = f"Accès refusé à {who} (id {user.id}) : {what}"
        try:
            await db.log_event(text, "warning")
            await svc.notifier.notify(
                f"<b>Tentative d'accès bloquée</b>\n\n{html.escape(who)} · id <code>{user.id}</code>\n{html.escape(what)}",
                "warning",
            )
        except Exception as exc:  # une alerte ne doit jamais casser la barrière
            log.debug("Alerte d'accès non envoyée : %s", exc)
