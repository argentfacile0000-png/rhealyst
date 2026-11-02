"""Notifications Telegram : progression live, validation des vidéos, publications."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from telegram import Bot, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError

from .. import db
from ..models import Publication, PubStatus, Video
from ..notify import STEPS, NullProgress, Progress
from .screens import video_caption, video_keyboard
from .ui import PLATFORM, bar, esc, kb, mmss, strip_platform

if TYPE_CHECKING:
    from ..core import Services

log = logging.getLogger(__name__)

STATE_ICON = {"wait": "○", "run": "●", "done": "✓", "fail": "✕"}


class TelegramProgress:
    """Un message Telegram mis à jour en direct pendant la génération."""

    def __init__(self, bot: Bot, chat_id: int, title: str):
        self.bot = bot
        self.chat_id = chat_id
        self.title = title
        self.message_id: int | None = None
        self.started = time.monotonic()
        self.states = {key: ("wait", "", None) for key, _ in STEPS}
        self.footer = ""
        self._lock = asyncio.Lock()
        self._last_text = ""

    def render(self) -> str:
        lines = [f"<b>{esc(self.title)}</b>", ""]
        for key, label in STEPS:
            state, detail, pct = self.states[key]
            text = f"<b>{label}</b>" if state == "run" else label
            lines.append(f"{STATE_ICON[state]}  {text}")
            if state == "run" and pct is not None:
                lines.append(f"    <code>{bar(pct)}</code>  {int(pct * 100)} %")
            if detail:
                lines.append(f"    <i>{esc(detail)}</i>")
        lines += ["", f"<i>{mmss(time.monotonic() - self.started)} écoulées</i>"]
        if self.footer:
            lines.append(self.footer)
        return "\n".join(lines)

    async def _push(self, final: bool = False, markup=None) -> None:
        text = self.render()
        if text == self._last_text and not final:
            return
        async with self._lock:
            try:
                if self.message_id is None:
                    msg = await self.bot.send_message(self.chat_id, text, parse_mode=ParseMode.HTML,
                                                      disable_notification=True)
                    self.message_id = msg.message_id
                else:
                    await self.bot.edit_message_text(text, chat_id=self.chat_id, message_id=self.message_id,
                                                     parse_mode=ParseMode.HTML, reply_markup=markup)
                self._last_text = text
            except RetryAfter as exc:
                if final:
                    await asyncio.sleep(float(getattr(exc, "retry_after", 3)) + 0.5)
                    await self._push(final=True, markup=markup)
            except BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    log.debug("Progress edit: %s", exc)
            except TelegramError as exc:
                log.debug("Progress: %s", exc)

    async def start(self) -> None:
        await self._push()

    async def update(self, step: str, state: str, detail: str = "", pct: float | None = None) -> None:
        if step not in self.states:
            return
        prev_detail = self.states[step][1]
        self.states[step] = (state, detail or (prev_detail if state == "run" else detail), pct)
        await self._push()

    async def finish(self, text: str, ok: bool = True) -> None:
        if not ok:
            for key, (state, detail, _pct) in self.states.items():
                if state == "run":
                    self.states[key] = ("fail", detail, None)
        self.footer = f"\n<b>{esc(text)}</b>"
        await self._push(final=True, markup=kb([[("File d'attente", "q"), ("Accueil", "h")]]))


class TelegramNotifier:
    def __init__(self, bot: Bot, svc: Services):
        self.bot = bot
        self.svc = svc

    @property
    def admins(self) -> list[int]:
        return sorted(self.svc.settings.admin_ids)

    async def progress(self, title: str, chat_id: int | None = None) -> Progress:
        if chat_id is None:
            if not await db.kv_get("live_auto", False) or not self.admins:
                return NullProgress()
            chat_id = self.admins[0]
        p = TelegramProgress(self.bot, chat_id, title)
        await p.start()
        return p

    async def notify(self, text: str, level: str = "info", channel_id: int | None = None) -> None:
        for chat in self.admins:
            kwargs = {"disable_notification": level == "info", "reply_markup": kb([[("Ouvrir le dashboard", "h")]])}
            try:
                await self.bot.send_message(chat, text, parse_mode=ParseMode.HTML, **kwargs)
            except BadRequest as exc:
                # Texte HTML refusé (caractère spécial dans un message d'erreur…) : l'alerte part quand même, en clair.
                log.warning("Notification HTML refusée (%s), envoi en texte brut", exc)
                try:
                    await self.bot.send_message(chat, html.unescape(re.sub(r"<[^>]+>", "", text))[:4000], **kwargs)
                except TelegramError as exc2:
                    log.warning("Notification impossible (%s): %s", chat, exc2)
            except TelegramError as exc:
                log.warning("Notification impossible (%s): %s", chat, exc)

    async def send_video_message(self, chat_id: int, video_id: int, *, nav: bool = False) -> int | None:
        v = await self.svc.get_video(video_id)
        if v is None:
            return None
        caption = await video_caption(self.svc, video_id)
        markup = video_keyboard(v, nav=nav)
        path = v.preview_path or v.file_path
        try:
            if path and Path(path).exists() and Path(path).stat().st_size < 49 * 1024 * 1024:
                with open(path, "rb") as fh:
                    msg = await self.bot.send_video(
                        chat_id, video=fh, caption=caption[:1024], parse_mode=ParseMode.HTML,
                        reply_markup=markup, supports_streaming=True, duration=int(v.duration),
                        width=1920 if v.format == "horizontal" else 1080,
                        height=1080 if v.format == "horizontal" else 1920,
                        read_timeout=180, write_timeout=180,
                    )
            else:
                msg = await self.bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML, reply_markup=markup)
            return msg.message_id
        except TelegramError as exc:
            log.warning("Envoi vidéo impossible: %s", exc)
            try:
                msg = await self.bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML, reply_markup=markup)
                return msg.message_id
            except TelegramError:
                return None

    async def video_for_approval(self, video_id: int) -> None:
        refs: list[list[int]] = []
        for chat in self.admins:
            mid = await self.send_video_message(chat, video_id)
            if mid:
                refs.append([chat, mid])
        async with db.session() as s:
            v = await s.get(Video, video_id)
            if v:
                v.tg_messages = refs

    async def refresh_video_messages(self, video_id: int) -> None:
        """Met à jour la légende/les boutons des messages d'approbation déjà envoyés."""
        v = await self.svc.get_video(video_id)
        if v is None or not v.tg_messages:
            return
        caption = (await video_caption(self.svc, video_id))[:1024]
        markup = video_keyboard(v, nav=False)
        for chat, mid in v.tg_messages:
            try:
                await self.bot.edit_message_caption(chat_id=chat, message_id=mid, caption=caption,
                                                    parse_mode=ParseMode.HTML, reply_markup=markup)
            except BadRequest as exc:
                if "there is no caption" in str(exc).lower() or "no text" in str(exc).lower():
                    try:
                        await self.bot.edit_message_text(caption, chat_id=chat, message_id=mid,
                                                         parse_mode=ParseMode.HTML, reply_markup=markup)
                    except TelegramError:
                        pass
            except TelegramError:
                pass

    async def video_published(self, video_id: int) -> None:
        await self.refresh_video_messages(video_id)
        async with db.session() as s:
            v = await s.get(Video, video_id)
            pubs = list((await s.execute(
                select(Publication).where(Publication.video_id == video_id)
            )).scalars())
        if v is None:
            return
        ok = [p for p in pubs if p.status in PubStatus.OK]
        part = f" · partie {v.part_index}/{v.part_count}" if v.part_count > 1 else ""
        lines = [f"<b>{'Publiée' if ok else 'Publication échouée'}</b> · #{v.id}",
                 f"{esc(v.title[:100])}<i>{part}</i>", ""]
        for p in pubs:
            name = PLATFORM.get(p.platform, p.platform)
            if p.status == PubStatus.PUBLISHED:
                state = f"✓ <a href=\"{esc(p.url)}\">voir la vidéo</a>" if p.url else "✓ en ligne"
            elif p.status == PubStatus.DRAFT:
                state = "✓ brouillon envoyé, <i>à finaliser dans l’app TikTok</i>"
            elif p.status == PubStatus.PROCESSING:
                state = "en traitement"
            else:
                state = f"✕ {esc(strip_platform(p.error, name)[:200])}"
            lines.append(f"{name}  {state}")
        rows = [[("Détails", f"v:{video_id}"), ("Dashboard", "h")]]
        if any(p.status == PubStatus.FAILED for p in pubs):
            rows.insert(0, [("Réessayer", f"vn:{video_id}")])
        for chat in self.admins:
            try:
                await self.bot.send_message(chat, "\n".join(lines), parse_mode=ParseMode.HTML,
                                            reply_markup=kb(rows), link_preview_options=LinkPreviewOptions(is_disabled=True),
                                            disable_notification=bool(ok))
            except TelegramError as exc:
                log.warning("Notification publication impossible: %s", exc)
