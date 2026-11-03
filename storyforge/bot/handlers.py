"""Handlers Telegram : commandes, navigation par boutons, saisies texte et fichiers."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from .. import db
from ..models import VideoFormat, VideoStatus
from ..publishers import PublisherError
from ..dates import parse_when
from ..tools import run_ytdlp, ytdlp_command, ytdlp_hint
from ..version import auto_update_flag
from ..video.background import VIDEO_EXTS
from . import screens
from .access import AccessGate
from .fields import FIELD_MAP
from .ui import PLATFORM, bar, esc, kb

if TYPE_CHECKING:
    from ..core import Services
    from .notifier import TelegramNotifier

log = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
CANCEL = [("Annuler", "x")]




def svc_of(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["svc"]


def notifier_of(context: ContextTypes.DEFAULT_TYPE) -> TelegramNotifier:
    return context.application.bot_data["notifier"]


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    admins = svc_of(context).settings.admin_ids
    if user and user.id in admins:
        return True
    if update.callback_query:
        await update.callback_query.answer("Accès refusé", show_alert=True)
    elif update.effective_message and user:
        if admins:
            await update.effective_message.reply_text("Accès refusé.")
        else:
            await update.effective_message.reply_html(
                f"Ton identifiant Telegram est <code>{user.id}</code>.\n"
                "Ajoute-le dans <code>TELEGRAM_ADMIN_IDS</code> du fichier <code>.env</code> puis redémarre le bot."
            )
    return False


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE, screen: screens.Screen, *, new: bool = False) -> None:
    """Affiche un écran : édite le message courant si possible, sinon en envoie un nouveau."""
    text, markup = screen
    q = update.callback_query
    if q and not new and q.message is not None and getattr(q.message, "text", None) is not None:
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, link_preview_options=NO_PREVIEW)
            return
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            log.debug("edit failed: %s", exc)
    await context.bot.send_message(update.effective_chat.id, text, parse_mode=ParseMode.HTML, reply_markup=markup,
                                   link_preview_options=NO_PREVIEW)


async def prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, state: dict) -> None:
    context.user_data["await"] = state
    await show(update, context, (text, kb([CANCEL])), new=update.callback_query is None)


# ---------------------------------------------------------------------------- commandes
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    context.user_data.pop("await", None)
    await show(update, context, await screens.home(svc_of(context)), new=True)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    context.user_data.pop("await", None)
    await update.effective_message.reply_text("Saisie annulée.", reply_markup=kb([[("Ouvrir le dashboard", "h")]]))


# ---------------------------------------------------------------------------- génération
def _missing_prereqs(svc: Services) -> list[str]:
    return svc.missing_prereqs()


async def start_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int, kind: str | None = None) -> None:
    svc = svc_of(context)
    q = update.callback_query
    missing = _missing_prereqs(svc)
    if missing:
        await q.answer("Il manque : " + ", ".join(missing), show_alert=True)
        return
    ch = await svc.get_channel(cid)
    if ch is None:
        await q.answer("Chaîne introuvable", show_alert=True)
        return
    if kind is None:
        enabled = ch.formats_enabled()
        if len(enabled) > 1:  # Shorts et vidéos longues : on demande le format
            await q.answer()
            await show(update, context, await screens.generate_kind(svc, cid))
            return
        kind = enabled[0] if enabled else VideoFormat.VERTICAL
    long_form = kind == VideoFormat.HORIZONTAL
    ahead = svc.jobs.count(kind="generate")
    await q.answer(("Vidéo longue lancée" if long_form else "Génération lancée")
                   + (f", après {ahead} tâche(s)" if ahead else ""))
    title = f"{'Vidéo longue' if long_form else 'Génération'} · {ch.name}"
    progress = await notifier_of(context).progress(title, chat_id=update.effective_chat.id)
    svc.enqueue_generate(cid, kind=kind, progress=progress, label=f"Manuel · {title}")


# ---------------------------------------------------------------------------- vidéos
async def refresh_video(update: Update, context: ContextTypes.DEFAULT_TYPE, vid: int) -> None:
    svc = svc_of(context)
    await notifier_of(context).refresh_video_messages(vid)
    q = update.callback_query
    msg = q.message if q else None
    if msg is not None and getattr(msg, "text", None) is not None:
        await show(update, context, await screens.video_view(svc, vid))
        return
    v = await svc.get_video(vid)
    if msg is not None and v is not None and [msg.chat_id, msg.message_id] not in (v.tg_messages or []):
        try:
            await q.edit_message_caption(caption=(await screens.video_caption(svc, vid))[:1024],
                                         parse_mode=ParseMode.HTML, reply_markup=screens.video_keyboard(v, nav=False))
        except TelegramError:
            pass


async def video_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, vid: int) -> None:
    svc = svc_of(context)
    q = update.callback_query
    v = await svc.get_video(vid)
    if v is None:
        await q.answer("Vidéo introuvable", show_alert=True)
        return
    if action == "va":
        if v.status != VideoStatus.PENDING_APPROVAL:
            await q.answer("Déjà traitée")
        else:
            await svc.approve(vid)
            await q.answer("Validée. Publication au prochain créneau.")
        await refresh_video(update, context, vid)
    elif action == "vn":
        if v.status in (VideoStatus.PUBLISHING, VideoStatus.RENDERING, VideoStatus.DISCARDED):
            await q.answer("Impossible dans l'état actuel", show_alert=True)
            return
        if not v.file_path:
            await q.answer("Fichier vidéo supprimé", show_alert=True)
            return
        ch = await svc.get_channel(v.channel_id)
        if not ch or not ch.platforms(v.format):
            await q.answer("Aucune plateforme liée/activée pour cette chaîne", show_alert=True)
            return
        job = svc.enqueue_publish(vid, v.channel_id)
        await q.answer("Publication lancée" if job else "Publication déjà en cours")
        if v.status == VideoStatus.PENDING_APPROVAL:
            await svc.approve(vid)
        await refresh_video(update, context, vid)
    elif action == "vx":
        await svc.discard(vid)
        await q.answer("Vidéo jetée")
        await refresh_video(update, context, vid)
    elif action == "vr":
        missing = _missing_prereqs(svc)
        if missing:
            await q.answer("Il manque : " + ", ".join(missing), show_alert=True)
            return
        await svc.discard(vid)
        await refresh_video(update, context, vid)
        await start_generation(update, context, v.channel_id, v.format)
    elif action == "vf":
        await q.answer("Envoi de l'aperçu…")
        await notifier_of(context).send_video_message(update.effective_chat.id, vid, nav=True)
    elif action == "vt":
        await q.answer()
        await show(update, context, await screens.video_view(svc, vid, with_text=True), new=True)


async def video_extra_action(update: Update, context: ContextTypes.DEFAULT_TYPE, head: str, parts: list[str]) -> None:
    """Archiver, programmer, découper."""
    svc = svc_of(context)
    q = update.callback_query
    vid = int(parts[1])
    v = await svc.get_video(vid)
    if v is None:
        await q.answer("Vidéo introuvable", show_alert=True)
        return
    as_new = q.message is not None and getattr(q.message, "text", None) is None
    try:
        if head == "var":
            await svc.archive(vid)
            await q.answer("Vidéo archivée")
            await refresh_video(update, context, vid)
        elif head == "vua":
            await svc.unarchive(vid)
            await q.answer("Vidéo désarchivée")
            await refresh_video(update, context, vid)
        elif head == "vsu":
            await svc.unschedule(vid)
            await q.answer("Programmation annulée")
            await refresh_video(update, context, vid)
        elif head == "vsc":
            ch = await svc.get_channel(v.channel_id)
            await q.answer()
            context.user_data["await"] = {"kind": "schedule", "vid": vid}
            await show(update, context, (screens.schedule_prompt(v, ch.timezone if ch else svc.settings.timezone),
                                         screens.schedule_keyboard(vid)), new=as_new)
        elif head == "vsq":
            ch = await svc.get_channel(v.channel_id)
            presets = {"t20": "aujourd'hui 20h", "d12": "demain 12h", "d18": "demain 18h", "d21": "demain 21h"}
            when = parse_when(presets.get(parts[2], "demain 18h"), ch.timezone if ch else svc.settings.timezone)
            await svc.schedule(vid, when)
            context.user_data.pop("await", None)
            await q.answer("Publication programmée")
            await refresh_video(update, context, vid)
        elif head == "vsp":
            await q.answer()
            await show(update, context, await screens.split_menu(svc, vid), new=as_new)
        elif head == "vsx":
            seconds = int(parts[2])
            chat_id = update.effective_chat.id

            async def run() -> None:
                try:
                    ids = await svc.split(vid, seconds)
                    text = f"✓ Vidéo #{vid} découpée en {len(ids)} parties : " + ", ".join(f"#{i}" for i in ids)
                except Exception as exc:
                    text = f"✕ Découpage de la vidéo #{vid} impossible : {exc}"
                await context.bot.send_message(chat_id, text,
                                               reply_markup=kb([[("Voir les vidéos", f"cvv:{v.channel_id}:0")]]))

            svc.jobs.submit("generate", f"Découpage #{vid}", run, channel_id=v.channel_id, tag="split")
            await q.answer(f"Découpage en parties de {seconds} s lancé")
    except ValueError as exc:
        await q.answer(str(exc)[:190], show_alert=True)


async def elevenlabs_keys_action(update: Update, context: ContextTypes.DEFAULT_TYPE, head: str, parts: list[str]) -> None:
    svc = svc_of(context)
    q = update.callback_query
    ring = svc.pipeline.tts.keys
    if head == "eka":
        await q.answer()
        await prompt(update, context, "<b>Ajouter une clé ElevenLabs</b>\n\nEnvoie la clé API (elevenlabs.io, profil, "
                     "API keys).\n<i>Ton message est supprimé dès réception ; la clé est vérifiée puis enregistrée "
                     "sur ton serveur.</i>", {"kind": "el_key"})
        return
    if head == "ekd":
        removed = await ring.remove(parts[1])
        svc._quota_cache = (0.0, None)
        await q.answer("Clé retirée" if removed else "Cette clé vient du .env : retire-la du fichier")
    elif head == "ekr":
        await ring.reactivate(parts[1])
        await q.answer("Clé réactivée")
    else:
        await q.answer()
    await show(update, context, await screens.elevenlabs_keys(svc))


# ---------------------------------------------------------------------------- comptes
# ---------------------------------------------------------------------------- tests
async def test_apis(svc: Services) -> screens.Screen:
    s = svc.settings
    lines = ["<b>État du système</b>", ""]

    def check(ok: bool, label: str, detail: str) -> None:
        lines.append(f"{'✓' if ok else '✕'}  {label}  <i>{esc(detail)}</i>")

    keys = svc.pipeline.llm_keys.all()
    if not keys:
        check(False, "IA des histoires", "aucune clé : Réglages, Clés API")
    for k in keys:
        try:
            check(True, f"IA {k.label} {k.masked}", await svc.pipeline.stories.ping_key(k))
        except Exception as exc:
            check(False, f"IA {k.label} {k.masked}", str(exc)[:150])
    try:
        sub = await svc.pipeline.tts.subscription()
        check(True, "ElevenLabs", f"forfait {sub.get('tier', '?')}, "
              f"{sub.get('character_count', 0)} sur {sub.get('character_limit', 0)} caractères utilisés")
    except Exception as exc:
        check(False, "ElevenLabs", str(exc)[:150])
    proc = await asyncio.create_subprocess_exec(s.ffmpeg_bin, "-version", stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    check(proc.returncode == 0, "ffmpeg", out.decode().splitlines()[0][:60] if proc.returncode == 0 else "introuvable")
    fonts = list(Path(s.fonts_dir).glob("*.ttf"))
    check(bool(fonts), "Polices", f"{len(fonts)} fichier(s)")
    cats = svc.pipeline.bank.categories()
    check(bool(cats), "Vidéos de fond", f"{sum(len(v) for v in cats.values())} fichier(s)")
    for platform, label in (("youtube", "YouTube"), ("tiktok", "TikTok"), ("instagram", "Instagram")):
        problem = svc.platform_problem(platform, 0)
        check(problem is None, label, "app configurée" if problem is None else problem)
    check(bool(ytdlp_command()), "yt-dlp", "ajout de fonds par lien")
    return "\n".join(lines), kb([[("Relancer", "stt"), ("← Réglages", "set")]])



# ---------------------------------------------------------------------------- routeur boutons
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    svc = svc_of(context)
    q = update.callback_query
    data = q.data or ""
    parts = data.split(":")
    head = parts[0]
    ints = [int(p) for p in parts[1:] if p.lstrip("-").isdigit()]
    cid = ints[0] if ints else 0

    simple = {
        "h": screens.home, "cl": screens.channel_list, "gen": screens.generate_picker, "q": screens.queue,
        "bk": screens.bank, "set": screens.settings_screen, "ev": screens.events, "md": screens.media,
        "bka": screens.bank_categories, "mu": screens.music_bank, "pf": screens.platforms, "ak": screens.api_keys,
    }
    if head in simple:
        await q.answer()
        await show(update, context, await simple[head](svc))
        return

    if head == "noop":
        await q.answer()
    elif head == "x":
        context.user_data.pop("await", None)
        await q.answer("Annulé")
        await show(update, context, await screens.home(svc))
    elif head == "new":
        await q.answer()
        await prompt(update, context, "<b>Nouvelle chaîne</b>\n\nComment s'appelle cette chaîne ?\n"
                     "<i>Exemples : Histoires Folles, Reddit Stories US</i>", {"kind": "new_channel"})
    elif head == "cv":
        await q.answer()
        await show(update, context, await screens.channel_view(svc, cid))
    elif head == "ct":
        ch = await svc.get_channel(cid)
        ch = await svc.update_channel(cid, active=not ch.active)
        await db.log_event(f"{ch.name} {'activée' if ch.active else 'mise en pause'}", "info", cid)
        if ch.active and not ch.platforms():
            await q.answer("Chaîne activée. Lie YouTube ou TikTok pour publier.", show_alert=True)
        else:
            await q.answer("Chaîne activée" if ch.active else "Chaîne en pause")
        if ch.active and not _missing_prereqs(svc):
            asyncio.create_task(svc.refill())
        await show(update, context, await screens.channel_view(svc, cid))
    elif head == "cg":
        kind = parts[2] if len(parts) > 2 and parts[2] in (VideoFormat.VERTICAL, VideoFormat.HORIZONTAL) else None
        await start_generation(update, context, cid, kind)
    elif head == "cp":
        v = await svc.next_ready_video(cid, kind=None)
        ch = await svc.get_channel(cid)
        if v is None:
            await q.answer("Aucune vidéo prête (validée) pour cette chaîne", show_alert=True)
        elif not ch.platforms(v.format):
            await q.answer("Aucune plateforme liée/activée", show_alert=True)
        else:
            svc.enqueue_publish(v.id, cid)
            await q.answer(f"Publication de #{v.id} lancée")
    elif head == "ce":
        await q.answer()
        await show(update, context, await screens.channel_view(svc, cid))
    elif head == "cE":
        await q.answer()
        await show(update, context, await screens.channel_group(svc, cid, parts[2]))
    elif head == "cf":
        await on_field(update, context, cid, parts[2])
    elif head == "co":
        f = FIELD_MAP[parts[2]]
        label, value = f.options[int(parts[3])]
        if value == "custom":  # valeur libre : on la demande en texte
            await q.answer()
            ch = await svc.get_channel(cid)
            await prompt(update, context, f"<b>{esc(f.label)}</b> · {esc(ch.name)}\n\nValeur actuelle : "
                         f"<b>{esc(f.value_text(ch))}</b>\n\n{f.hint}", {"kind": "field", "cid": cid, "key": f.key})
            return
        await svc.update_channel(cid, **f.updates_for(value))
        await q.answer(f"{f.label} : {label}")
        await show(update, context, await screens.channel_group(svc, cid, f.group))
    elif head == "cm":
        await on_multi(update, context, cid, parts[2], int(parts[3]))
    elif head == "vp":
        await q.answer()
        voices = await _voices(context)
        await show(update, context, await screens.voice_picker(svc, cid, voices, int(parts[2])))
    elif head == "vsel":
        voices = await _voices(context)
        v = voices[int(parts[2])]
        await svc.update_channel(cid, voice_id=v["voice_id"], voice_name=v["name"])
        await q.answer(f"Voix : {v['name']}")
        await show(update, context, await screens.channel_group(svc, cid, "voice"))
    elif head == "cu":
        platform = parts[2]
        await svc.unlink(cid, platform)
        context.user_data.pop("await", None)
        await q.answer(f"{PLATFORM.get(platform, platform)} déconnecté")
        await show(update, context, await screens.accounts(svc, cid))
    elif head == "ca":
        await q.answer()
        await show(update, context, await screens.accounts(svc, cid))
    elif head == "cd":
        ch = await svc.get_channel(cid)
        await q.answer()
        await show(update, context, (
            f"<b>Supprimer « {esc(ch.name)} » ?</b>\n\nHistoires, vidéos et historique de cette chaîne "
            "seront effacés de la base. Les comptes YouTube/TikTok ne sont pas touchés.",
            kb([[("Supprimer définitivement", f"cD:{cid}"), ("Annuler", f"cv:{cid}")]]),
        ))
    elif head == "cD":
        await svc.delete_channel(cid)
        await q.answer("Chaîne supprimée")
        await show(update, context, await screens.channel_list(svc))
    elif head == "cvv":
        await q.answer()
        await show(update, context, await screens.channel_videos(svc, cid, int(parts[2]) if len(parts) > 2 else 0))
    elif head == "cva":
        await q.answer()
        await show(update, context, await screens.channel_videos(svc, cid, int(parts[2]) if len(parts) > 2 else 0,
                                                                 archived=True))
    elif head == "v":
        await q.answer()
        is_media = q.message is not None and getattr(q.message, "text", None) is None
        await show(update, context, await screens.video_view(svc, cid), new=is_media)
    elif head in ("va", "vn", "vx", "vr", "vf", "vt"):
        await video_action(update, context, head, cid)
    elif head in ("var", "vua", "vsc", "vsq", "vsu", "vsp", "vsx"):
        await video_extra_action(update, context, head, parts)
    elif head in ("aka", "akd", "aku", "akr", "akm", "akmp", "akms", "akmo", "akg", "akgs", "akgo"):
        await api_key_action(update, context, head, parts)
    elif head in ("sp0", "spf", "spe", "spr"):
        await story_prompt_action(update, context, head)
    elif head in ("vm", "vml"):
        v = await svc.get_video(cid)
        await q.answer()
        if v is not None:
            try:
                await q.edit_message_reply_markup(screens.video_keyboard(v, more=head == "vm"))
            except BadRequest:
                pass
    elif head in ("ek", "eka", "ekd", "ekr"):
        await elevenlabs_keys_action(update, context, head, parts)
    elif head == "bkc":
        cats = sorted(svc.pipeline.bank.categories().keys())
        idx = int(parts[1])
        if idx >= len(cats):
            await q.answer("Catégorie introuvable", show_alert=True)
            return
        await q.answer()
        await _ask_bg(update, context, cats[idx])
    elif head in ("pfy", "pft", "pfi", "pfg", "pfk", "pfx", "pfv", "pfvx", "pftd"):
        await platform_action(update, context, head, parts)
    elif head in ("yt", "yta", "ytx", "ytp", "ytpx", "ytt", "ytl", "ytlt", "ytlx"):
        await ytdlp_action(update, context, head)
    elif head in ("mua", "mut", "mup", "mud", "muD", "mue"):
        await music_action(update, context, head, parts)
    elif head == "dlx":
        event = DOWNLOADS.get(parts[1] if len(parts) > 1 else "")
        if event is None:
            await q.answer("Ce téléchargement est déjà terminé")
            return
        event.set()
        await q.answer("Annulation…")
    elif head in ("lkc", "lkn", "lkm"):
        await link_destination(update, context, head, parts)
    elif head == "bkn":
        await q.answer()
        await prompt(update, context, "<b>Nouvelle catégorie</b>\n\nQuel nom ?\n<i>Exemples : cooking, minecraft, satisfying</i>",
                     {"kind": "bg_newcat"})
    elif head == "sp":
        paused = await svc.is_paused()
        await svc.set_paused(not paused)
        await q.answer("Système relancé" if paused else "Système en pause")
        from_settings = q.message is not None and (q.message.text or "").startswith("Réglages système")
        await show(update, context, await (screens.settings_screen(svc) if from_settings else screens.home(svc)))
    elif head == "sau":
        flag = auto_update_flag(svc.settings.data_dir)
        if flag.exists():
            flag.unlink()
            await q.answer("Mises à jour automatiques réactivées")
        else:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("1")
            await q.answer("Mises à jour automatiques suspendues")
        await db.log_event("Mises à jour auto " + ("suspendues" if flag.exists() else "réactivées"), "info")
        await show(update, context, await screens.settings_screen(svc))
    elif head == "stt":
        await q.answer("Diagnostic en cours…")
        await show(update, context, await test_apis(svc))
    else:
        await q.answer()


async def _voices(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    voices = context.user_data.get("voices")
    if not voices:
        voices = await svc_of(context).pipeline.tts.list_voices()
        voices.sort(key=lambda v: (v.get("category") != "cloned", v["name"].lower()))
        context.user_data["voices"] = voices
    return voices


async def on_field(update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int, key: str) -> None:
    svc = svc_of(context)
    q = update.callback_query
    f = FIELD_MAP.get(key)
    if f is None:
        await q.answer()
        return
    ch = await svc.get_channel(cid)
    if f.kind == "toggle":
        new = not getattr(ch, key)
        await svc.update_channel(cid, **{key: new})
        await q.answer(f"{f.label} : {'activé' if new else 'désactivé'}")
        await show(update, context, await screens.channel_group(svc, cid, f.group))
    elif f.kind in ("choice", "multi"):
        await q.answer()
        cats = sorted(svc.pipeline.bank.categories().keys())
        await show(update, context, await screens.field_choices(svc, cid, key, bg_categories=cats))
    elif f.kind == "voice":
        try:
            context.user_data.pop("voices", None)
            voices = await _voices(context)
        except Exception as exc:
            await q.answer(f"ElevenLabs : {exc}"[:190], show_alert=True)
            return
        await q.answer()
        await show(update, context, await screens.voice_picker(svc, cid, voices, 0))
    else:
        await q.answer()
        current = f.value_text(ch)
        await prompt(update, context,
                     f"<b>{esc(f.label)}</b> · {esc(ch.name)}\n\nValeur actuelle : <b>{esc(current)}</b>\n\n{f.hint or 'Envoie la nouvelle valeur.'}",
                     {"kind": "field", "cid": cid, "key": key})


async def on_multi(update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int, key: str, idx: int) -> None:
    svc = svc_of(context)
    q = update.callback_query
    ch = await svc.get_channel(cid)
    if key in ("bg_categories", "music_tracks"):
        opts = (sorted(svc.pipeline.bank.categories().keys()) if key == "bg_categories"
                else svc.pipeline.music.names())
        if idx >= len(opts):
            await q.answer()
            return
        value = opts[idx]
    else:
        value = FIELD_MAP[key].options[idx][1]
    current = list(getattr(ch, key) or [])
    if value in current:
        current.remove(value)
    else:
        current.append(value)
    if key == "formats" and not current:
        await q.answer("Garde au moins un format", show_alert=True)
        return
    await svc.update_channel(cid, **{key: current})
    await q.answer()
    cats = sorted(svc.pipeline.bank.categories().keys())
    await show(update, context, await screens.field_choices(svc, cid, key, bg_categories=cats))


async def _ask_bg(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str) -> None:
    await prompt(
        update, context,
        f"<b>Ajouter une vidéo</b> · {esc(category)}\n\n"
        "Envoie une vidéo ou un fichier de 20 Mo maximum (limite des bots Telegram), ou un lien téléchargé avec yt-dlp.\n\n"
        "<i>Les vidéos longues (20 à 60 min) offrent plus de variété. Utilise des contenus dont tu as les droits.</i>",
        {"kind": "bg_add", "category": category},
    )


# ---------------------------------------------------------------------------- saisies texte
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    svc = svc_of(context)
    text = (update.effective_message.text or "").strip()
    state = context.user_data.get("await")
    msg = update.effective_message

    if state is None:
        if re.fullmatch(r"https?://\S+", text):
            await offer_link(update, context, text)  # un lien envoyé à tout moment : où l'ajouter ?
            return
        else:
            await msg.reply_html("Tout se pilote depuis le dashboard.", reply_markup=kb([[("Ouvrir le dashboard", "h")]]))
            return

    kind = state["kind"]
    try:
        if kind == "new_channel":
            if not 2 <= len(text) <= 60:
                raise ValueError("Le nom doit faire entre 2 et 60 caractères")
            ch = await svc.create_channel(text)
            context.user_data.pop("await", None)
            await msg.reply_html(f"Chaîne <b>{esc(ch.name)}</b> créée. Choisis sa langue.")
            text_, markup = await screens.field_choices(svc, ch.id, "language")
            await show(update, context, (text_, markup), new=True)
        elif kind == "field":
            f = FIELD_MAP[state["key"]]
            updates = f.parse(text) if f.parse else {f.key: text}
            await svc.update_channel(state["cid"], **updates)
            context.user_data.pop("await", None)
            await msg.reply_html(f"<b>{esc(f.label)}</b> mis à jour.")
            await show(update, context, await screens.channel_group(svc, state["cid"], f.group), new=True)
        elif kind == "voice_id":
            if not re.fullmatch(r"[A-Za-z0-9]{10,40}", text):
                raise ValueError("ID de voix invalide")
            voices = context.user_data.get("voices") or []
            name = next((v["name"] for v in voices if v["voice_id"] == text), f"voix {text[:6]}…")
            await svc.update_channel(state["cid"], voice_id=text, voice_name=name)
            context.user_data.pop("await", None)
            await show(update, context, await screens.channel_group(svc, state["cid"], "voice"), new=True)
        elif kind == "schedule":
            v = await svc.get_video(state["vid"])
            ch = await svc.get_channel(v.channel_id) if v else None
            when = parse_when(text, ch.timezone if ch else svc.settings.timezone)
            await svc.schedule(state["vid"], when)
            context.user_data.pop("await", None)
            await notifier_of(context).refresh_video_messages(state["vid"])
            await show(update, context, await screens.video_view(svc, state["vid"]), new=True)
        elif kind == "el_key":
            try:  # la clé ne reste pas dans la conversation
                await msg.delete()
            except (TelegramError, AttributeError):
                pass
            if not re.fullmatch(r"[A-Za-z0-9_\-]{20,100}", text):
                raise ValueError("Ce n'est pas une clé ElevenLabs valide")
            try:
                sub = await svc.pipeline.tts.subscription(api_key=text)
            except Exception as exc:
                raise ValueError(f"Clé refusée par ElevenLabs : {exc}") from exc
            await svc.pipeline.tts.keys.add(text)
            svc._quota_cache = (0.0, None)
            context.user_data.pop("await", None)
            left = int(sub.get("character_limit", 0)) - int(sub.get("character_count", 0))
            await context.bot.send_message(update.effective_chat.id,
                                           f"✓ Clé ajoutée : {max(0, left)} caractères disponibles.")
            await show(update, context, await screens.elevenlabs_keys(svc), new=True)
        elif kind == "bg_newcat":
            cat = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")[:40]
            if not cat:
                raise ValueError("Nom invalide")
            (svc.settings.backgrounds_dir / cat).mkdir(parents=True, exist_ok=True)
            if state.get("link"):  # catégorie créée pour un lien déjà envoyé
                context.user_data.pop("await", None)
                background(context, update.effective_chat.id,
                           _download_bg(context, update.effective_chat.id, state["link"], cat))
            else:
                await _ask_bg(update, context, cat)
        elif kind == "app_key":
            await receive_app_key(update, context, state, text)
        elif kind == "ytdlp_proxies":
            await save_proxy_list(update, context, text)
        elif kind == "ytdlp_proxy" and len([x for x in text.splitlines() if x.strip()]) > 1:
            await save_proxy_list(update, context, text)  # plusieurs adresses collées : c'est une liste
        elif kind == "ytdlp_proxy":
            from ..tools import PROXY_KEY, check_proxy

            try:  # peut contenir un mot de passe
                await msg.delete()
            except (TelegramError, AttributeError):
                pass
            await db.kv_set(PROXY_KEY, check_proxy(text))
            context.user_data.pop("await", None)
            await db.log_event("Proxy de téléchargement enregistré")
            await show(update, context, await screens.ytdlp_settings(svc), new=True)
        elif kind == "ytdlp_cookies":
            raise ValueError("Envoie le fichier cookies.txt lui-même (trombone, Fichier)")
        elif kind == "story_prompt":
            await save_story_prompt(update, context, update.effective_message.text or "")
        elif kind == "api_key_model":
            if not re.fullmatch(r"[\w.:/\-@]{2,160}", text):
                raise ValueError("Identifiant de modèle invalide")
            await _set_model(update, context, state["kid"], text)
        elif kind == "aws_region":
            if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", text.strip()):
                raise ValueError("Région invalide (exemple : eu-west-3)")
            await _set_region(update, context, state.get("kid", ""), text.strip())
        elif kind == "api_key":
            await receive_api_key(update, context, text)
        elif kind == "site_verify":
            await publish_verification(update, context, None, text)
        elif kind == "google_json":
            raise ValueError("Envoie le fichier JSON lui-même (trombone, Fichier), pas son contenu")
        elif kind == "music_add":
            if not re.match(r"https?://", text):
                raise ValueError("Envoie un fichier audio ou un lien http(s)")
            context.user_data.pop("await", None)
            background(context, update.effective_chat.id, _download_music(context, update.effective_chat.id, text))
        elif kind == "bg_add":
            if not re.match(r"https?://", text):
                raise ValueError("Envoie une vidéo ou un lien http(s)")
            context.user_data.pop("await", None)
            background(context, update.effective_chat.id,
                       _download_bg(context, update.effective_chat.id, text, state["category"]))
        else:
            context.user_data.pop("await", None)
    except (ValueError, PublisherError) as exc:
        await msg.reply_html(f"{esc(exc)}\n<i>Réessaie, ou /cancel pour annuler.</i>")


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    state = context.user_data.get("await") or {}
    msg = update.effective_message
    if state.get("kind") == "music_add":
        await receive_music(update, context)
        return
    if state.get("kind") == "google_json":
        await receive_google_client(update, context)
        return
    if state.get("kind") == "story_prompt":
        doc = msg.document
        if doc is None or (doc.file_size or 0) > 100_000:
            await msg.reply_html("Envoie le prompt en texte, ou un fichier .txt (100 Ko maximum).")
            return
        raw = bytes(await (await doc.get_file()).download_as_bytearray())
        try:
            await save_story_prompt(update, context, raw.decode("utf-8-sig", "replace"))
        except ValueError as exc:
            await msg.reply_html(f"{esc(exc)}\n<i>Réessaie, ou /cancel pour annuler.</i>")
        return
    if state.get("kind") == "ytdlp_proxies":
        doc = msg.document
        if doc is None or (doc.file_size or 0) > 512 * 1024:
            await msg.reply_html("Envoie un fichier .txt (512 Ko maximum).\n<i>/cancel pour annuler.</i>")
            return
        raw = bytes(await (await doc.get_file()).download_as_bytearray())
        try:
            await save_proxy_list(update, context, raw.decode("utf-8-sig", "replace"), doc.file_name or "")
        except ValueError as exc:
            await msg.reply_html(f"{esc(exc)}\n<i>Réessaie, ou /cancel pour annuler.</i>")
        return
    if state.get("kind") == "ytdlp_cookies":
        await receive_ytdlp_cookies(update, context)
        return
    if state.get("kind") == "site_verify":
        doc = msg.document
        if doc is None or (doc.file_size or 0) > 2048:
            await msg.reply_html("Envoie le petit fichier .txt de vérification.\n<i>/cancel pour annuler.</i>")
            return
        raw = bytes(await (await doc.get_file()).download_as_bytearray())
        try:
            await publish_verification(update, context, doc.file_name, raw.decode("utf-8-sig", "replace"))
        except ValueError as exc:
            await msg.reply_html(f"{esc(exc)}\n<i>Réessaie, ou /cancel pour annuler.</i>")
        return
    if msg.audio or msg.voice:
        await msg.reply_html("Pour ajouter une bande son, passe par l'accueil, Bande son, puis Ajouter une piste.",
                             reply_markup=kb([[("Bande son", "mu")]]))
        return
    if state.get("kind") != "bg_add":
        await msg.reply_html("Pour ajouter un fond, passe par Banque vidéo, puis Ajouter une vidéo.",
                             reply_markup=kb([[("Banque vidéo", "bk")]]))
        return
    media = msg.video or msg.document
    if media is None:
        return
    name = getattr(media, "file_name", None) or f"video_{media.file_unique_id}.mp4"
    if Path(name).suffix.lower() not in VIDEO_EXTS:
        name = Path(name).stem + ".mp4"
    if (media.file_size or 0) > 20 * 1024 * 1024:
        await msg.reply_html("Ce fichier dépasse 20 Mo : les bots Telegram ne peuvent pas le télécharger.\n"
                             "Envoie plutôt un lien, ou dépose-le directement dans <code>data/backgrounds/</code>.")
        return
    svc = svc_of(context)
    dest_dir = svc.settings.backgrounds_dir / state["category"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\- ]+", "_", Path(name).name).lstrip(".") or f"video_{media.file_unique_id}.mp4"
    dest = dest_dir / safe
    if dest.exists():  # ne jamais écraser un fond existant
        dest = dest_dir / f"{dest.stem}-{media.file_unique_id}{dest.suffix}"
    info = await msg.reply_text("Téléchargement…")
    tg_file = await media.get_file()
    await tg_file.download_to_drive(custom_path=dest)
    context.user_data.pop("await", None)
    dur = await svc.pipeline.bank.duration(dest)
    await info.edit_text(f"✓ Ajoutée à « {state['category']} », {int(dur // 60)}:{int(dur % 60):02d}",
                         reply_markup=kb([[("Banque vidéo", "bk"), ("En ajouter une autre", f"bkc:{_cat_index(svc, state['category'])}")]]))


def background(context: ContextTypes.DEFAULT_TYPE, chat_id: int, coro) -> asyncio.Task:
    """Tâche de fond (téléchargement, test…) : une erreur inattendue est signalée au lieu de disparaître."""
    async def runner() -> None:
        try:
            await coro
        except Exception as exc:
            log.exception("Tâche de fond échouée")
            try:
                await context.bot.send_message(chat_id, f"✕ Erreur inattendue : {exc}"[:400])
            except TelegramError:
                pass

    return asyncio.create_task(runner())


async def offer_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    """Lien reçu hors de tout formulaire : on propose de l'ajouter à la banque vidéo ou à la bande son."""
    svc = svc_of(context)
    context.user_data["pending_link"] = url
    cats = sorted(svc.pipeline.bank.categories().keys())
    rows = [[(f"Fond · {c}", f"lkc:{i}")] for i, c in enumerate(cats[:12])]
    rows.append([("Fond · nouvelle catégorie", "lkn")])
    rows.append([("Bande son (musique)", "lkm")])
    rows.append(CANCEL)
    await update.effective_message.reply_html(
        "<b>Que faire de ce lien ?</b>\n\nAjoute-le à la <b>banque vidéo</b> (choisis la catégorie) "
        "ou à la <b>bande son</b>.", reply_markup=kb(rows), link_preview_options=NO_PREVIEW)


async def link_destination(update: Update, context: ContextTypes.DEFAULT_TYPE, head: str, parts: list[str]) -> None:
    svc = svc_of(context)
    q = update.callback_query
    url = context.user_data.pop("pending_link", None)
    chat_id = update.effective_chat.id
    if not url:
        await q.answer("Renvoie le lien", show_alert=True)
        return
    if head == "lkm":
        await q.answer("Téléchargement de la piste")
        background(context, chat_id, _download_music(context, chat_id, url))
    elif head == "lkn":
        await q.answer()
        await prompt(update, context, "<b>Nouvelle catégorie</b>\n\nQuel nom ?\n<i>Exemples : cooking, minecraft, "
                     "satisfying</i>", {"kind": "bg_newcat", "link": url})
    else:
        cats = sorted(svc.pipeline.bank.categories().keys())
        idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
        if not 0 <= idx < len(cats):
            await q.answer("Catégorie introuvable", show_alert=True)
            return
        await q.answer(f"Téléchargement dans {cats[idx]}")
        background(context, chat_id, _download_bg(context, chat_id, url, cats[idx]))


def _cat_index(svc: Services, category: str) -> int:
    cats = sorted(svc.pipeline.bank.categories().keys())
    return cats.index(category) if category in cats else 0


DOWNLOADS: dict[str, asyncio.Event] = {}  # téléchargements en cours (jeton -> annulation)


async def ytdlp_download(context: ContextTypes.DEFAULT_TYPE, chat_id: int, url: str, *, dest_dir: Path,
                         label: str, args: list[str]):
    """Télécharge avec yt-dlp en affichant l'avancement (bouton Annuler). Retourne (fichier final, message)."""
    import secrets
    import time

    svc = svc_of(context)
    if not ytdlp_command():
        await context.bot.send_message(chat_id, "yt-dlp n'est pas installé : relance install.sh sur le serveur")
        return None, None
    token = secrets.token_hex(4)
    cancel = DOWNLOADS[token] = asyncio.Event()
    work = svc.settings.work_dir / f"dl-{token}"  # les morceaux partiels restent ici, jamais dans la banque
    work.mkdir(parents=True, exist_ok=True)
    stop_kb = kb([[("Annuler", f"dlx:{token}")]])
    info = await context.bot.send_message(chat_id, f"Téléchargement {label}…\nPréparation.", reply_markup=stop_kb)
    last = {"t": 0.0, "text": ""}

    async def progress(p: dict) -> None:
        now = time.monotonic()
        if p.get("phase") == "download" and now - last["t"] < 4 and p["percent"] < 100:
            return
        lines = [f"Téléchargement {label}…"]
        if p.get("phase") == "start":
            lines.append("Connexion à la vidéo…")
        else:
            lines.append(f"{bar(p['percent'] / 100, 12)}  {p['percent']:.0f} %")
            lines.append(" · ".join(x for x in (p.get("speed"), f"reste {p['eta']}" if p.get("eta") else "",
                                                 p.get("size")) if x) or "")
            if p["percent"] >= 99.9:
                lines.append("Assemblage de la vidéo…")
        if p.get("proxy"):
            lines.append(f"Proxy {p['attempt']}/{p['attempts']}")
        text = "\n".join(x for x in lines if x)
        if text == last["text"]:
            return
        last.update(t=now, text=text)
        try:
            await info.edit_text(text, reply_markup=stop_kb)
        except TelegramError:
            pass

    try:
        with svc.jobs.hold():  # la mise à jour automatique attend la fin du téléchargement
            res = await run_ytdlp(svc.settings, [
                *args, "--no-playlist", "--restrict-filenames", "-o", str(work / "%(title).60s-%(id)s.%(ext)s"),
                "--print", "after_move:filepath", "--no-warnings", "--", url,
            ], on_progress=progress, cancel=cancel)
        if res.cancelled:
            await info.edit_text("✕ Téléchargement annulé.")
            return None, info
        lines = [x.strip() for x in res.out.decode("utf-8", "replace").splitlines() if x.strip()]
        found = next((Path(x) for x in reversed(lines) if Path(x).is_file()), None)
        if not res.ok or found is None:
            tried = f" ({res.attempts} proxies essayés)" if res.proxy else ""
            await info.edit_text(f"✕ Échec du téléchargement{tried} : {ytdlp_hint(res.error or 'aucun fichier')}")
            return None, info
        dest_dir.mkdir(parents=True, exist_ok=True)
        final = dest_dir / found.name
        if final.exists():
            final = dest_dir / f"{found.stem}-{token}{found.suffix}"
        shutil.move(str(found), final)
        return final, info
    finally:
        DOWNLOADS.pop(token, None)
        shutil.rmtree(work, ignore_errors=True)


async def _download_bg(context: ContextTypes.DEFAULT_TYPE, chat_id: int, url: str, category: str) -> None:
    svc = svc_of(context)
    path, info = await ytdlp_download(
        context, chat_id, url, dest_dir=svc.settings.backgrounds_dir / category, label=f"dans « {category} »",
        args=["-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
              "--merge-output-format", "mp4"],
    )
    if path is None:
        return
    dur = await svc.pipeline.bank.duration(path)
    await info.edit_text(f"✓ Ajoutée : {path.name}, {int(dur // 60)} min {int(dur % 60):02d} s",
                         reply_markup=kb([[("Banque vidéo", "bk")]]))
    await db.log_event(f"Nouveau fond dans « {category} » : {path.name}")


# ---------------------------------------------------------------------------- clés API
ELEVEN_RE = re.compile(r"sk_[0-9a-f]{40,64}")


async def api_key_action(update: Update, context: ContextTypes.DEFAULT_TYPE, head: str, parts: list[str]) -> None:
    svc = svc_of(context)
    q = update.callback_query
    ring = svc.pipeline.llm_keys
    kid = parts[1] if len(parts) > 1 else ""
    if head == "aka":
        await q.answer()
        await prompt(update, context, "<b>Ajouter une clé</b>\n\nColle une clé d'IA (Gemini, Groq, OpenAI, OpenRouter, AWS Bedrock) "
                     "ou ElevenLabs : je reconnais le fournisseur, je la vérifie puis je supprime ton message.\n\n"
                     "<i>Gratuit : Gemini sur aistudio.google.com/apikey, Groq sur console.groq.com/keys.</i>",
                     {"kind": "api_key"})
        return
    if head == "akd":
        await ring.remove(kid)
        await db.log_event("Clé d'IA retirée")
        await q.answer("Clé retirée")
    elif head == "aku":
        await ring.move_up(kid)
        await q.answer("Clé remontée")
    elif head == "akr":
        await ring.reactivate(kid)
        await q.answer("Clé réactivée")
    elif head in ("akm", "akmp"):
        key = ring.get(kid)
        if key is None:
            await q.answer("Clé introuvable", show_alert=True)
            return
        await q.answer("Lecture des modèles…" if head == "akm" else None)
        cache = context.user_data.setdefault("models", {})
        if head == "akm" or kid not in cache:
            cache[kid] = await _models_for(svc, key)
        page = int(parts[2]) if head == "akmp" and len(parts) > 2 and parts[2].isdigit() else 0
        await show(update, context, screens.api_key_models(svc, kid, cache[kid], page))
        return
    elif head == "akms":
        models = context.user_data.get("models", {}).get(kid) or []
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
        if not 0 <= idx < len(models):
            await q.answer("Rouvre la liste des modèles", show_alert=True)
            return
        await q.answer(f"Modèle : {models[idx]}"[:190])
        await _set_model(update, context, kid, models[idx])
        return
    elif head == "akmo":
        await q.answer()
        await prompt(update, context, "<b>Autre modèle</b>\n\nEnvoie l'identifiant exact du modèle.\n<i>Exemples : "
                     "gemini-2.5-pro, gpt-4.1-mini, anthropic.claude-sonnet-5-5, eu.anthropic.claude-sonnet-5-5</i>",
                     {"kind": "api_key_model", "kid": kid})
        return
    elif head == "akg":
        await q.answer()
        await show(update, context, screens.region_picker(kid))
        return
    elif head == "akgs":
        from ..story.llm_keys import BEDROCK_REGIONS

        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
        if not 0 <= idx < len(BEDROCK_REGIONS):
            await q.answer()
            return
        await q.answer()
        await _set_region(update, context, kid, BEDROCK_REGIONS[idx][0])
        return
    elif head == "akgo":
        await q.answer()
        await prompt(update, context, "<b>Région AWS</b>\n\nEnvoie le code de la région.\n<i>Exemples : us-east-2, "
                     "eu-north-1, ap-northeast-1</i>", {"kind": "aws_region", "kid": kid})
        return
    await show(update, context, await screens.api_keys(svc))


def _rank_models(key, listed: list[str]) -> list[str]:
    """Suggestions connues en tête, puis les modèles gratuits, puis le reste (suggestions seules si liste vide)."""
    from ..story.llm_keys import SUGGESTED_MODELS

    suggested = SUGGESTED_MODELS.get(key.provider, [])
    if not listed:
        return suggested
    first = [m for m in suggested if m in listed]
    free = [m for m in listed if m.endswith(":free") and m not in first]
    return first + free + [m for m in listed if m not in first and m not in free]


async def _models_for(svc: Services, key) -> list[str]:
    return _rank_models(key, await svc.pipeline.stories.list_models(key))


async def _set_model(update: Update, context: ContextTypes.DEFAULT_TYPE, kid: str, model: str) -> None:
    from ..story.generator import KeyFailure

    svc = svc_of(context)
    key = await svc.pipeline.llm_keys.update(kid, model=model)
    if key is None:
        raise ValueError("Clé introuvable")
    context.user_data.pop("await", None)
    try:
        note = f"✓ Modèle choisi et vérifié : {await svc.pipeline.stories.ping_key(key)}"
    except KeyFailure as exc:
        note = f"! Modèle enregistré, mais le test a échoué ({exc.reason}) : {exc}"[:600]
    except Exception as exc:
        note = f"! Modèle enregistré, test impossible : {exc}"[:400]
    await context.bot.send_message(update.effective_chat.id, note)
    await show(update, context, await screens.api_keys(svc), new=True)


async def _set_region(update: Update, context: ContextTypes.DEFAULT_TYPE, kid: str, region: str) -> None:
    """Région d'une clé AWS : termine l'ajout d'une nouvelle clé, ou change celle d'une clé existante."""
    from ..story import bedrock

    svc = svc_of(context)
    ring = svc.pipeline.llm_keys
    chat = update.effective_chat.id
    context.user_data.pop("await", None)
    listed: list[str] = []
    if not kid:
        key = context.user_data.pop("aws_key", None)
        if not key:
            await context.bot.send_message(chat, "Renvoie ta clé AWS : Réglages, Clés API, Ajouter une clé.")
            return
        try:
            listed = await bedrock.list_models(key, region)
        except bedrock.BedrockError as exc:
            if exc.reason == "invalide":
                await context.bot.send_message(chat, f"✕ {exc}")
                return
        kid = (await ring.add(key, "bedrock", region=region)).kid
        await db.log_event(f"Clé AWS Bedrock ajoutée ({region})", "success")
        await context.bot.send_message(chat, f"✓ Clé AWS Bedrock ajoutée ({region}). Choisis maintenant le modèle.")
    else:
        await ring.update(kid, region=region)
    key = ring.get(kid)
    cache = context.user_data.setdefault("models", {})
    cache[kid] = _rank_models(key, listed) if listed else await _models_for(svc, key)
    await show(update, context, screens.api_key_models(svc, kid, cache[kid]), new=True)


async def receive_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    from ..story.generator import KeyFailure
    from ..story.llm_keys import LLMKey, detect_provider

    svc = svc_of(context)
    msg = update.effective_message
    try:  # la clé ne reste pas dans la conversation
        await msg.delete()
    except (TelegramError, AttributeError):
        pass
    key = text.strip()
    chat = update.effective_chat.id
    if detect_provider(key) == "bedrock":
        if not re.fullmatch(r"[A-Za-z0-9+/=_\-.:%&]{20,4000}", key):
            raise ValueError("Clé AWS Bedrock illisible : colle-la telle que la console AWS l'affiche")
        context.user_data["aws_key"] = key
        context.user_data.pop("await", None)
        await show(update, context, screens.region_picker(""), new=True)
        return
    if ELEVEN_RE.fullmatch(key):
        try:
            sub = await svc.pipeline.tts.subscription(api_key=key)
        except Exception as exc:
            raise ValueError(f"Clé refusée par ElevenLabs : {exc}") from exc
        await svc.pipeline.tts.keys.add(key)
        svc._quota_cache = (0.0, None)
        left = int(sub.get("character_limit", 0)) - int(sub.get("character_count", 0))
        note = f"✓ Clé ElevenLabs ajoutée : {max(0, left)} caractères disponibles."
    else:
        provider = detect_provider(key)
        if provider is None or not re.fullmatch(r"[A-Za-z0-9_\-.]{20,200}", key):
            raise ValueError("Clé non reconnue. Formats acceptés : Gemini (AIza…), Groq (gsk_…), OpenAI (sk-…), "
                             "OpenRouter (sk-or-…), AWS Bedrock (ABSK… ou bedrock-api-key-…), ElevenLabs (sk_…)")
        candidate = LLMKey(key, provider, "bot")
        try:
            model = await svc.pipeline.stories.ping_key(candidate)
            note = f"✓ Clé {candidate.label} ajoutée et vérifiée ({model})."
        except KeyFailure as exc:
            if exc.reason == "invalide":
                raise ValueError(f"Clé refusée par {candidate.label} : {exc}") from exc
            note = f"✓ Clé {candidate.label} ajoutée ({exc.reason} pour le moment, elle servira plus tard)."
        except Exception as exc:
            note = f"✓ Clé {candidate.label} ajoutée (vérification impossible : {str(exc)[:80]})."
        await svc.pipeline.llm_keys.add(key, provider)
    context.user_data.pop("await", None)
    await db.log_event(note.lstrip("✓ "), "success")
    await context.bot.send_message(chat, note)
    await show(update, context, await screens.api_keys(svc), new=True)


# ---------------------------------------------------------------------------- plateformes (apps développeur)
APP_STEPS = {
    "tiktok": [("tiktok_client_key", "le <b>Client key</b>"), ("tiktok_client_secret", "le <b>Client secret</b>")],
    "instagram": [("instagram_app_id", "l'<b>ID de l'app Instagram</b>"),
                  ("instagram_app_secret", "la <b>clé secrète de l'app Instagram</b>")],
}


async def platform_action(update: Update, context: ContextTypes.DEFAULT_TYPE, head: str, parts: list[str]) -> None:
    svc = svc_of(context)
    q = update.callback_query
    if head in ("pfy", "pft", "pfi"):
        await q.answer()
        platform = {"pfy": "youtube", "pft": "tiktok", "pfi": "instagram"}[head]
        await show(update, context, await screens.platform_guide(svc, platform))
    elif head == "pfg":
        await q.answer()
        await prompt(update, context, "<b>Client OAuth Google</b>\n\nEnvoie le fichier JSON téléchargé depuis Google "
                     "Cloud (<i>client_secret_….json</i>), avec le trombone, Fichier.\n\n"
                     "<i>Le message est supprimé après lecture : le fichier reste uniquement sur ton serveur.</i>",
                     {"kind": "google_json"})
    elif head == "pfk" and len(parts) > 1 and parts[1] in APP_STEPS:
        await q.answer()
        platform = parts[1]
        await prompt(update, context, f"<b>{PLATFORM[platform]}</b> · 1/2\n\nEnvoie {APP_STEPS[platform][0][1]}.",
                     {"kind": "app_key", "platform": platform, "values": {}})
    elif head == "pfv":
        await q.answer()
        await prompt(update, context, "<b>Vérification du site TikTok</b>\n\nEnvoie le fichier de vérification "
                     "téléchargé depuis TikTok (<i>tiktok….txt</i>), ou colle la ligne "
                     "<code>tiktok-developers-site-verification=…</code>.", {"kind": "site_verify"})
    elif head == "pftd":
        direct = not svc.apps.tiktok_direct
        await svc.apps.set_tiktok_direct(direct)
        await q.answer("Publication directe : reconnecte les comptes TikTok" if direct else "Envoi en brouillon")
        await show(update, context, await screens.platform_guide(svc, "tiktok"))
    elif head == "pfvx":
        from ..platform_apps import clear_verifications

        await clear_verifications()
        await q.answer("Fichiers retirés")
        await show(update, context, await screens.platform_guide(svc, "tiktok"))
    elif head == "pfx" and len(parts) > 1 and parts[1] in PLATFORM:
        await svc.apps.clear(parts[1])
        await q.answer("Identifiants retirés")
        await show(update, context, await screens.platform_guide(svc, parts[1]))


async def publish_verification(update: Update, context: ContextTypes.DEFAULT_TYPE, filename: str | None,
                               content: str) -> None:
    from ..platform_apps import add_verification, parse_verification

    svc = svc_of(context)
    name, value = parse_verification(filename, content)
    await add_verification(name, value)
    context.user_data.pop("await", None)
    url = f"{svc.settings.public_base}/{name}"
    await context.bot.send_message(
        update.effective_chat.id,
        f"✓ Fichier publié : <code>{esc(url)}</code>\n\nDans TikTok, méthode <b>URL prefix</b> avec "
        f"<code>{esc(svc.settings.public_base)}/</code>, puis clique <i>Verify</i>.",
        parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW,
    )
    await show(update, context, await screens.platform_guide(svc, "tiktok"), new=True)


async def receive_app_key(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict, text: str) -> None:
    from ..platform_apps import check_value
    from ..publishers import tiktok

    svc = svc_of(context)
    msg = update.effective_message
    try:  # une clé ne reste pas dans la conversation
        await msg.delete()
    except (TelegramError, AttributeError):
        pass
    platform = state["platform"]
    steps = APP_STEPS[platform]
    values = dict(state.get("values") or {})
    key, _ = steps[len(values)]
    values[key] = check_value(key, text)
    if len(values) < len(steps):
        state["values"] = values
        context.user_data["await"] = state
        await context.bot.send_message(update.effective_chat.id,
                                       f"{PLATFORM[platform]} · 2/2\n\nEnvoie {steps[len(values)][1]}.",
                                       parse_mode=ParseMode.HTML, reply_markup=kb([CANCEL]))
        return
    note = ""
    if platform == "tiktok":
        try:
            ok = await tiktok.check_client(values["tiktok_client_key"], values["tiktok_client_secret"])
        except PublisherError as exc:
            context.user_data["await"] = {"kind": "app_key", "platform": platform, "values": {}}
            raise ValueError(f"{exc}. Vérifie les deux valeurs et recommence : envoie le Client key") from exc
        note = "" if ok else "\n<i>TikTok n'a pas pu être joint pour vérifier les clés : elles sont enregistrées quand même.</i>"
    await svc.apps.set(platform, values)
    context.user_data.pop("await", None)
    await context.bot.send_message(update.effective_chat.id,
                                   f"✓ App {PLATFORM[platform]} enregistrée. Connecte maintenant ton compte : "
                                   f"Chaîne, puis Comptes.{note}", parse_mode=ParseMode.HTML)
    await show(update, context, await screens.platform_guide(svc, platform), new=True)


async def story_prompt_action(update: Update, context: ContextTypes.DEFAULT_TYPE, head: str) -> None:
    import io

    from ..story.prompts import SYSTEM_PROMPT

    svc = svc_of(context)
    q = update.callback_query
    chat = update.effective_chat.id
    text = svc.pipeline.stories.system_prompt or SYSTEM_PROMPT
    if head == "spf":
        await q.answer()
        if len(text) <= 4000:  # à copier directement depuis Telegram
            await context.bot.send_message(chat, text)
        await context.bot.send_document(chat, document=io.BytesIO(text.encode("utf-8")),
                                        filename="prompt-histoires.txt", caption="Prompt système des histoires")
        return
    if head == "spe":
        await q.answer()
        await prompt(update, context, "<b>Modifier le prompt des histoires</b>\n\nEnvoie le nouveau prompt système en "
                     "texte, ou un fichier .txt s'il dépasse la taille d'un message.\n\n<i>Il remplace le prompt "
                     "actuel pour toutes les chaînes. « Rétablir l'original » reste disponible.</i>",
                     {"kind": "story_prompt"})
        return
    if head == "spr":
        await svc.set_story_prompt(None)
        await q.answer("Prompt original rétabli")
    else:
        await q.answer()
    await show(update, context, screens.story_prompt(svc))


async def save_story_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    svc = svc_of(context)
    text = text.strip()
    if len(text) < 50:
        raise ValueError("Prompt trop court : envoie le prompt complet (50 caractères minimum)")
    if len(text) > 30000:
        raise ValueError("Prompt trop long (30 000 caractères maximum)")
    await svc.set_story_prompt(text)
    context.user_data.pop("await", None)
    warn = "" if "json" in text.lower() else ("\n\n! Le prompt ne mentionne plus le JSON : l'IA doit répondre en JSON "
                                               "(title, parts, hashtags…), sinon la génération échouera.")
    await context.bot.send_message(update.effective_chat.id,
                                   f"✓ Prompt des histoires enregistré ({len(text)} caractères).{warn}")
    await show(update, context, screens.story_prompt(svc), new=True)


async def receive_google_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = svc_of(context)
    msg = update.effective_message
    doc = msg.document
    if doc is None or (doc.file_size or 0) > 64 * 1024:
        await msg.reply_html("Envoie le fichier JSON du client OAuth (quelques Ko).\n<i>/cancel pour annuler.</i>")
        return
    raw = bytes(await (await doc.get_file()).download_as_bytearray())
    try:
        await msg.delete()
    except TelegramError:
        pass
    redirect = f"{svc.settings.public_base}/oauth/youtube" if svc.portal_available() else ""
    try:
        warnings = await svc.apps.save_google(raw, redirect)
    except ValueError as exc:
        await context.bot.send_message(update.effective_chat.id, f"✕ {exc}\n\nRenvoie le bon fichier, ou /cancel.")
        return
    context.user_data.pop("await", None)
    extra = "".join(f"\n\n<i>{esc(w)}</i>" for w in warnings)
    await context.bot.send_message(update.effective_chat.id,
                                   "✓ Client OAuth Google enregistré. Connecte maintenant ta chaîne YouTube : "
                                   f"Chaîne, puis Comptes.{extra}", parse_mode=ParseMode.HTML)
    await show(update, context, await screens.platform_guide(svc, "youtube"), new=True)


# ---------------------------------------------------------------------------- téléchargement par lien (yt-dlp)
async def ytdlp_action(update: Update, context: ContextTypes.DEFAULT_TYPE, head: str) -> None:
    from ..tools import PROXY_KEY, TEST_URL, cookies_path, mask_proxy, proxy_state, set_proxy_list

    svc = svc_of(context)
    q = update.callback_query
    if head == "yt":
        await q.answer()
    elif head == "yta":
        await q.answer()
        await prompt(update, context, "<b>Cookies YouTube</b>\n\nEnvoie le fichier <i>cookies.txt</i> exporté depuis "
                     "youtube.com (trombone, Fichier).\n\n<i>Le message est supprimé après lecture : le fichier reste "
                     "uniquement sur ton serveur.</i>", {"kind": "ytdlp_cookies"})
        return
    elif head == "ytx":
        cookies_path(svc.settings).unlink(missing_ok=True)
        await db.log_event("Cookies YouTube retirés")
        await q.answer("Cookies retirés")
    elif head == "ytp":
        await q.answer()
        await prompt(update, context, "<b>Proxy</b>\n\nEnvoie l'adresse du proxy.\n<i>Exemples : "
                     "http://utilisateur:motdepasse@hote:port, socks5://hote:1080. Le message est supprimé "
                     "après lecture.</i>", {"kind": "ytdlp_proxy"})
        return
    elif head == "ytpx":
        await db.kv_set(PROXY_KEY, "")
        await q.answer("Proxy retiré")
    elif head == "ytt":
        exe = ytdlp_command()
        if not exe:
            await q.answer("yt-dlp n'est pas installé", show_alert=True)
            return
        await q.answer("Test en cours…")
        info = await context.bot.send_message(update.effective_chat.id, "Test de téléchargement YouTube…")
        res = await run_ytdlp(svc.settings, ["--simulate", "--no-playlist", "--print", "title", "--quiet",
                                             "--no-warnings", "--", TEST_URL], timeout=90)
        via = f" via {mask_proxy(res.proxy)}" if res.proxy else ""
        if res.ok and res.out.strip():
            await info.edit_text(f"✓ YouTube répond{via} : « {res.out.decode('utf-8', 'replace').strip()[:80]} ». "
                                 "Les ajouts par lien devraient fonctionner.")
        else:
            tried = f" ({res.attempts} proxies essayés)" if res.proxy else ""
            await info.edit_text(f"✕ YouTube refuse encore{tried} : {ytdlp_hint(res.error)}")
        return
    elif head == "ytl":
        await q.answer()
        await prompt(update, context, "<b>Liste de proxies</b>\n\nEnvoie un fichier <i>.txt</i> avec une adresse par "
                     "ligne : <code>ip:port</code>, <code>utilisateur:motdepasse@ip:port</code>, "
                     "<code>ip:port:utilisateur:motdepasse</code> ou une adresse complète "
                     "(<code>socks5://ip:port</code>). Un fichier nommé <i>socks5.txt</i> ou <i>socks4.txt</i> "
                     "est lu comme des proxies SOCKS.\n\n<i>Tu peux aussi coller la liste dans un message.</i>",
                     {"kind": "ytdlp_proxies"})
        return
    elif head == "ytlt":
        st = await proxy_state()
        if not st["list"]:
            await q.answer("Aucune liste enregistrée", show_alert=True)
            return
        await q.answer("Test lancé")
        background(context, update.effective_chat.id, _test_proxy_list(context, update.effective_chat.id))
        return
    elif head == "ytlx":
        await set_proxy_list([])
        await db.log_event("Liste de proxies retirée")
        await q.answer("Liste retirée")
    await show(update, context, await screens.ytdlp_settings(svc))


async def save_proxy_list(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, filename: str = "") -> None:
    from ..tools import parse_proxy_list, set_proxy_list

    urls = parse_proxy_list(text, filename)
    if not urls:
        raise ValueError("Aucune adresse de proxy reconnue (une par ligne, ex. 1.2.3.4:8080)")
    await set_proxy_list(urls)
    context.user_data.pop("await", None)
    await db.log_event(f"Liste de {len(urls)} proxies enregistrée")
    await context.bot.send_message(update.effective_chat.id, f"✓ {len(urls)} proxies enregistrés. Test en cours : "
                                   "je garde ceux qui joignent YouTube.")
    background(context, update.effective_chat.id, _test_proxy_list(context, update.effective_chat.id))


async def _test_proxy_list(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    from ..tools import test_proxy_list

    svc = svc_of(context)
    info = await context.bot.send_message(chat_id, "Test des proxies…")

    async def progress(done: int, total: int) -> None:
        try:
            await info.edit_text(f"Test des proxies… {done}/{total}")
        except TelegramError:
            pass

    ok, total = await test_proxy_list(progress)
    text = (f"✓ {ok} proxies sur {total} joignent YouTube. Ils seront essayés à tour de rôle ; si l'un échoue, "
            "le suivant prend le relais." if ok else
            f"✕ Aucun des {total} proxies ne joint YouTube. Les proxies gratuits meurent vite : essaie une liste "
            "plus récente.")
    try:
        await info.edit_text(text)
    except TelegramError:
        await context.bot.send_message(chat_id, text)
    await show_in_chat(context, chat_id, await screens.ytdlp_settings(svc))


async def show_in_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int, screen: screens.Screen) -> None:
    text, markup = screen
    await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=markup,
                                   link_preview_options=NO_PREVIEW)


async def receive_ytdlp_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ..tools import save_cookies

    svc = svc_of(context)
    msg = update.effective_message
    doc = msg.document
    if doc is None or (doc.file_size or 0) > 1_000_000:
        await msg.reply_html("Envoie le fichier cookies.txt (quelques Ko).\n<i>/cancel pour annuler.</i>")
        return
    raw = bytes(await (await doc.get_file()).download_as_bytearray())
    try:
        await msg.delete()
    except TelegramError:
        pass
    try:
        count = save_cookies(svc.settings, raw)
    except ValueError as exc:
        await context.bot.send_message(update.effective_chat.id, f"✕ {exc}\n\nRenvoie le bon fichier, ou /cancel.")
        return
    context.user_data.pop("await", None)
    await db.log_event("Cookies YouTube enregistrés pour les téléchargements par lien", "success")
    await context.bot.send_message(update.effective_chat.id,
                                   f"✓ Cookies enregistrés ({count} cookies YouTube/Google). Appuie sur « Tester avec "
                                   "YouTube » pour vérifier.")
    await show(update, context, await screens.ytdlp_settings(svc), new=True)


# ---------------------------------------------------------------------------- bande son
MUSIC_PROMPT = (
    "<b>Ajouter une piste</b>\n\n"
    "Envoie un fichier audio (mp3, m4a, wav, ogg, flac) de 20 Mo maximum, une vidéo dont on garde le son, "
    "ou un lien (YouTube, SoundCloud…).\n\n"
    "<i>Utilise des musiques libres de droits ou dont tu as la licence : YouTube et TikTok bloquent ou "
    "démonétisent les vidéos qui contiennent des musiques protégées.</i>"
)


async def music_action(update: Update, context: ContextTypes.DEFAULT_TYPE, head: str, parts: list[str]) -> None:
    svc = svc_of(context)
    q = update.callback_query
    music = svc.pipeline.music
    idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
    names = music.names()
    name = names[idx] if 0 <= idx < len(names) else None
    if head == "mua":
        await q.answer()
        await prompt(update, context, MUSIC_PROMPT, {"kind": "music_add"})
    elif head == "mut":
        await q.answer()
        await show(update, context, await screens.music_track(svc, idx))
    elif head == "mup":
        path = music.resolve(name) if name else None
        if path is None:
            await q.answer("Piste introuvable", show_alert=True)
            return
        if path.stat().st_size > 49 * 1024 * 1024:
            await q.answer("Fichier trop lourd pour Telegram (50 Mo maximum)", show_alert=True)
            return
        await q.answer("Envoi de la piste…")
        with path.open("rb") as fh:
            await context.bot.send_audio(update.effective_chat.id, audio=fh, filename=path.name,
                                         title=path.stem[:60], disable_notification=True)
    elif head == "mud":
        if name is None:
            await q.answer("Piste introuvable", show_alert=True)
            return
        await q.answer()
        await show(update, context, (
            f"<b>Supprimer « {esc(name)} » ?</b>\n\nLa piste est retirée de la banque et des chaînes qui l'utilisent.",
            kb([[("Supprimer", f"muD:{idx}"), ("Annuler", f"mut:{idx}")]]),
        ))
    elif head == "muD":
        path = music.resolve(name) if name else None
        if path is None:
            await q.answer("Piste introuvable", show_alert=True)
            return
        path.unlink(missing_ok=True)
        for ch in await svc.list_channels():
            if name in (ch.music_tracks or []):
                await svc.update_channel(ch.id, music_tracks=[t for t in ch.music_tracks if t != name])
        await db.log_event(f"Piste supprimée de la bande son : {name}")
        await q.answer("Piste supprimée")
        await show(update, context, await screens.music_bank(svc))
    elif head == "mue":
        n = 0
        for ch in await svc.list_channels():
            if ch.music_volume <= 0:
                await svc.update_channel(ch.id, music_volume=0.08)
                n += 1
        await db.log_event(f"Bande son activée sur {n} chaîne(s)")
        await q.answer(f"Bande son activée sur {n} chaîne{'s' if n > 1 else ''}")
        await show(update, context, await screens.music_bank(svc))


def _music_dest(svc: Services, name: str, unique: str) -> Path:
    stem = re.sub(r"[^\w.\- ]+", "_", Path(name).stem).strip(" .") or f"piste_{unique}"
    suffix = Path(name).suffix.lower()
    dest_dir = svc.settings.music_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem[:80]}{suffix}"
    if dest.exists():  # ne jamais écraser une piste existante
        dest = dest_dir / f"{stem[:70]}-{unique}{suffix}"
    return dest


async def _music_added(context: ContextTypes.DEFAULT_TYPE, svc: Services, path: Path, info) -> None:
    """Vérifie la piste, puis confirme (ou la retire si elle est illisible)."""
    try:
        dur = await svc.pipeline.music.duration(path)
    except Exception:
        dur = 0.0
    if dur <= 1:
        path.unlink(missing_ok=True)
        await info.edit_text("✕ Ce fichier ne contient pas de son lisible.")
        return
    await db.log_event(f"Nouvelle piste dans la bande son : {path.name}")
    chs = await svc.list_channels()
    rows = [[("Bande son", "mu"), ("En ajouter une autre", "mua")]]
    if chs and not any(c.music_volume > 0 for c in chs):
        rows.insert(0, [("Activer sur toutes les chaînes (8 %)", "mue")])
    await info.edit_text(f"✓ Piste ajoutée : {path.name}, {int(dur // 60)}:{int(dur % 60):02d}", reply_markup=kb(rows))


async def receive_music(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ..video.music import AUDIO_EXTS

    svc = svc_of(context)
    msg = update.effective_message
    media = msg.audio or msg.voice or msg.video or msg.document
    if media is None:
        return
    unique = media.file_unique_id
    name = getattr(media, "file_name", None) or (f"voix_{unique}.ogg" if msg.voice else f"piste_{unique}.mp3")
    ext = Path(name).suffix.lower()
    is_video = msg.video is not None or ext in VIDEO_EXTS
    if not is_video and ext not in AUDIO_EXTS:
        await msg.reply_html("Format non pris en charge : envoie un mp3, m4a, wav, ogg ou flac, "
                             "ou une vidéo dont on garde le son.\n<i>/cancel pour annuler.</i>")
        return
    if (media.file_size or 0) > 20 * 1024 * 1024:
        await msg.reply_html("Ce fichier dépasse 20 Mo : les bots Telegram ne peuvent pas le télécharger.\n"
                             "Envoie plutôt un lien, ou dépose-le dans <code>data/music/</code>.")
        return
    info = await msg.reply_text("Téléchargement…")
    tg_file = await media.get_file()
    context.user_data.pop("await", None)
    if is_video:  # on ne garde que le son
        work = svc.settings.work_dir
        work.mkdir(parents=True, exist_ok=True)
        tmp = work / f"music-{unique}{ext or '.mp4'}"
        await tg_file.download_to_drive(custom_path=tmp)
        dest = _music_dest(svc, Path(name).stem + ".m4a", unique)
        proc = await asyncio.create_subprocess_exec(
            svc.settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(tmp), "-vn", "-c:a", "aac", "-b:a", "192k",
            str(dest), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        tmp.unlink(missing_ok=True)
        if proc.returncode != 0:
            dest.unlink(missing_ok=True)
            await info.edit_text(f"✕ Son introuvable dans cette vidéo : {err.decode('utf-8', 'replace')[-200:]}")
            return
    else:
        dest = _music_dest(svc, name, unique)
        await tg_file.download_to_drive(custom_path=dest)
    await _music_added(context, svc, dest, info)


async def _download_music(context: ContextTypes.DEFAULT_TYPE, chat_id: int, url: str) -> None:
    svc = svc_of(context)
    path, info = await ytdlp_download(
        context, chat_id, url, dest_dir=svc.settings.music_dir, label="de la piste",
        args=["-f", "bestaudio/best", "-x", "--audio-format", "m4a", "--max-filesize", "200M"],
    )
    if path is not None:
        await _music_added(context, svc, path, info)


# ---------------------------------------------------------------------------- erreurs
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Erreur Telegram", exc_info=context.error)
    if isinstance(update, Update) and update.callback_query:
        try:
            await update.callback_query.answer(str(context.error)[:190], show_alert=True)
        except TelegramError:
            pass


def register(app: Application) -> None:
    # Barrière globale (groupe -1) : rien ne passe sans être administrateur, en conversation privée.
    app.add_handler(TypeHandler(Update, AccessGate()), group=-1)
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler(["start", "dashboard", "menu"], cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(private & (filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL),
                                   on_file))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
