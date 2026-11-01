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
    filters,
)

from .. import db
from ..models import VideoStatus
from ..publishers import PublisherError
from ..video.background import VIDEO_EXTS
from . import screens
from .fields import FIELD_MAP
from .ui import esc, kb

if TYPE_CHECKING:
    from ..core import Services
    from .notifier import TelegramNotifier

log = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
CANCEL = [("Annuler", "x")]

HELP = """<b>Aide</b>

StoryForge fabrique des vidéos <i>storytime</i> : histoire écrite par ChatGPT, voix ElevenLabs, fond tiré au hasard dans ta banque (séquence aléatoire de la bonne durée), sous-titres mot à mot, puis publication sur YouTube Shorts et TikTok.

<b>Commandes</b>
/start  Dashboard
/channels  Chaînes
/new  Nouvelle chaîne
/generate  Générer maintenant
/queue  File d'attente et planning
/stats  Statistiques
/bank  Banque vidéo
/settings  Réglages système
/log  Journal
/cancel  Annuler une saisie
/myid  Ton identifiant Telegram

<b>Pour commencer</b>
1. Ajoute des vidéos de fond, idéalement longues (20 à 60 min).
2. Crée une chaîne : langue, formats, voix.
3. Lie YouTube et/ou TikTok.
4. Active-la : la réserve se remplit et les créneaux publient seuls.
"""


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


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    await update.effective_message.reply_html(HELP, reply_markup=kb([[("Ouvrir le dashboard", "h")]]))


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        await update.effective_message.reply_html(f"🆔 <code>{update.effective_user.id}</code>")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    await update.effective_message.reply_text("Saisie annulée.", reply_markup=kb([[("Ouvrir le dashboard", "h")]]))


def screen_cmd(builder):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update, context):
            return
        await show(update, context, await builder(svc_of(context)), new=True)

    return handler


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    await prompt(update, context, "<b>Nouvelle chaîne</b>\n\nComment s'appelle cette chaîne ?\n"
                 "<i>Exemples : Histoires Folles, Reddit Stories US</i>", {"kind": "new_channel"})


# ---------------------------------------------------------------------------- génération
def _missing_prereqs(svc: Services) -> list[str]:
    return svc.missing_prereqs()


async def start_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int) -> None:
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
    ahead = svc.jobs.count(kind="generate")
    await q.answer("Génération lancée" + (f", après {ahead} tâche(s)" if ahead else ""))
    progress = await notifier_of(context).progress(f"Génération · {ch.name}", chat_id=update.effective_chat.id)
    svc.enqueue_generate(cid, progress=progress, label=f"Manuel · {ch.name}")


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
        if not ch or not ch.platforms():
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
        await start_generation(update, context, v.channel_id)
    elif action == "vf":
        await q.answer("Envoi de l'aperçu…")
        await notifier_of(context).send_video_message(update.effective_chat.id, vid, nav=True)
    elif action == "vt":
        await q.answer()
        await show(update, context, await screens.video_view(svc, vid, with_text=True), new=True)


# ---------------------------------------------------------------------------- comptes
async def link_account(update: Update, context: ContextTypes.DEFAULT_TYPE, cid: int, platform: str) -> None:
    svc = svc_of(context)
    q = update.callback_query
    ch = await svc.get_channel(cid)
    s = svc.settings
    try:
        url = svc.youtube_auth_url(cid) if platform == "youtube" else svc.tiktok_auth_url(cid)
    except PublisherError as exc:
        await q.answer()
        doc = ("Crée un client OAuth « Application de bureau » dans Google Cloud Console (API YouTube Data v3 activée), "
               "télécharge le JSON et place-le dans <code>secrets/google_client_secret.json</code>."
               if platform == "youtube" else
               "Crée une app sur developers.tiktok.com (Login Kit + Content Posting API), puis renseigne "
               "<code>TIKTOK_CLIENT_KEY</code>, <code>TIKTOK_CLIENT_SECRET</code> et <code>TIKTOK_REDIRECT_URI</code>.")
        await show(update, context, (f"<b>Configuration manquante</b>\n\n{esc(exc)}\n\n{doc}",
                                     kb([[("← Chaîne", f"cv:{cid}")]])))
        return
    await q.answer()
    name = "YouTube" if platform == "youtube" else "TikTok"
    auto = platform == "youtube" and bool(s.webapp_url)
    steps = [
        f"<b>Lier {name}</b> · {esc(ch.name)}",
        "",
        f"1. Ouvre le lien et connecte le compte {name} de cette chaîne.",
    ]
    if auto:
        steps.append("2. C'est tout : la liaison est automatique, je te préviens ici.")
    else:
        steps += [
            "2. Après l'autorisation, une page s'ouvre. <i>Une erreur « site inaccessible » est normale.</i>",
            "3. Copie l'adresse complète de cette page et colle-la ici.",
        ]
    linked = ch.youtube_linked if platform == "youtube" else ch.tiktok_linked
    rows = [[(f"Autoriser {name}", url)]]
    if linked:
        rows.append([(f"Délier {name}", f"cu:{cid}:{platform}")])
    rows.append(CANCEL)
    context.user_data["await"] = {"kind": "oauth", "cid": cid, "platform": platform}
    await show(update, context, ("\n".join(steps), kb(rows)))


# ---------------------------------------------------------------------------- tests
async def test_apis(svc: Services) -> screens.Screen:
    s = svc.settings
    lines = ["<b>Diagnostic</b>", ""]

    def check(ok: bool, label: str, detail: str) -> None:
        lines.append(f"{'✓' if ok else '✕'}  {label}  <i>{esc(detail)}</i>")

    try:
        await svc.pipeline.stories.client.models.retrieve(s.openai_model)
        check(True, "OpenAI", f"modèle {s.openai_model} accessible")
    except Exception as exc:
        check(False, "OpenAI", str(exc)[:150])
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
    has_google = s.google_client_secrets_file.exists()
    check(has_google, "OAuth Google", "présent" if has_google else "secrets/google_client_secret.json manquant")
    tt_ok = bool(s.tiktok_client_key and s.tiktok_client_secret and s.tiktok_redirect_uri)
    check(tt_ok, "TikTok", "configuré" if tt_ok else "clés manquantes")
    check(bool(shutil.which("yt-dlp")), "yt-dlp", "ajout de fonds par lien")
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
        "st": screens.stats, "bk": screens.bank, "set": screens.settings_screen, "ev": screens.events,
        "bka": screens.bank_categories,
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
        await start_generation(update, context, cid)
    elif head == "cp":
        v = await svc.next_ready_video(cid)
        ch = await svc.get_channel(cid)
        if v is None:
            await q.answer("Aucune vidéo prête (validée) pour cette chaîne", show_alert=True)
        elif not ch.platforms():
            await q.answer("Aucune plateforme liée/activée", show_alert=True)
        else:
            svc.enqueue_publish(v.id, cid)
            await q.answer(f"Publication de #{v.id} lancée")
    elif head == "ce":
        await q.answer()
        await show(update, context, await screens.channel_edit(svc, cid))
    elif head == "cE":
        await q.answer()
        await show(update, context, await screens.channel_group(svc, cid, parts[2]))
    elif head == "cf":
        await on_field(update, context, cid, parts[2])
    elif head == "co":
        f = FIELD_MAP[parts[2]]
        label, value = f.options[int(parts[3])]
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
    elif head == "vid":
        await q.answer()
        await prompt(update, context, "<b>ID de voix</b>\n\nEnvoie l'identifiant de la voix ElevenLabs.\n<i>Dans ElevenLabs : Voices, puis l'icône ID.</i>",
                     {"kind": "voice_id", "cid": cid})
    elif head == "cy":
        await link_account(update, context, cid, "youtube")
    elif head == "ck":
        await link_account(update, context, cid, "tiktok")
    elif head == "cu":
        platform = parts[2]
        fields = ({"youtube_credentials": None, "youtube_account": ""} if platform == "youtube"
                  else {"tiktok_credentials": None, "tiktok_account": ""})
        await svc.update_channel(cid, **fields)
        context.user_data.pop("await", None)
        await q.answer("Compte délié")
        await show(update, context, await screens.channel_view(svc, cid))
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
    elif head == "v":
        await q.answer()
        is_media = q.message is not None and getattr(q.message, "text", None) is None
        await show(update, context, await screens.video_view(svc, cid), new=is_media)
    elif head in ("va", "vn", "vx", "vr", "vf", "vt"):
        await video_action(update, context, head, cid)
    elif head == "bkc":
        cats = sorted(svc.pipeline.bank.categories().keys())
        idx = int(parts[1])
        if idx >= len(cats):
            await q.answer("Catégorie introuvable", show_alert=True)
            return
        await q.answer()
        await _ask_bg(update, context, cats[idx])
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
    elif head == "slv":
        live = bool(await db.kv_get("live_auto", False))
        await db.kv_set("live_auto", not live)
        await q.answer("Suivi en direct " + ("désactivé" if live else "activé"))
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
    if key == "bg_categories":
        opts = sorted(svc.pipeline.bank.categories().keys())
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
        if "code=" in text and "state=" in text:
            state = {"kind": "oauth"}
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
        elif kind == "oauth":
            info = await msg.reply_text("Vérification de l'autorisation…")
            platform, ch, account = await svc.complete_oauth_from_url(text)
            context.user_data.pop("await", None)
            await info.edit_text(f"✓ {'YouTube' if platform == 'youtube' else 'TikTok'} lié à « {ch.name} » : {account}")
            await show(update, context, await screens.channel_view(svc, ch.id), new=True)
        elif kind == "bg_newcat":
            cat = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")[:40]
            if not cat:
                raise ValueError("Nom invalide")
            (svc.settings.backgrounds_dir / cat).mkdir(parents=True, exist_ok=True)
            await _ask_bg(update, context, cat)
        elif kind == "bg_add":
            if not re.match(r"https?://", text):
                raise ValueError("Envoie une vidéo ou un lien http(s)")
            context.user_data.pop("await", None)
            asyncio.create_task(_download_bg(context, update.effective_chat.id, text, state["category"]))
        else:
            context.user_data.pop("await", None)
    except (ValueError, PublisherError) as exc:
        await msg.reply_html(f"{esc(exc)}\n<i>Réessaie, ou /cancel pour annuler.</i>")


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update, context):
        return
    state = context.user_data.get("await") or {}
    msg = update.effective_message
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
    dest = dest_dir / re.sub(r"[^\w.\- ]+", "_", name)
    info = await msg.reply_text("Téléchargement…")
    tg_file = await media.get_file()
    await tg_file.download_to_drive(custom_path=dest)
    context.user_data.pop("await", None)
    dur = await svc.pipeline.bank.duration(dest)
    await info.edit_text(f"✓ Ajoutée à « {state['category']} », {int(dur // 60)}:{int(dur % 60):02d}",
                         reply_markup=kb([[("Banque vidéo", "bk"), ("En ajouter une autre", f"bkc:{_cat_index(svc, state['category'])}")]]))


def _cat_index(svc: Services, category: str) -> int:
    cats = sorted(svc.pipeline.bank.categories().keys())
    return cats.index(category) if category in cats else 0


async def _download_bg(context: ContextTypes.DEFAULT_TYPE, chat_id: int, url: str, category: str) -> None:
    svc = svc_of(context)
    exe = shutil.which("yt-dlp")
    if not exe:
        await context.bot.send_message(chat_id, "yt-dlp n'est pas installé : pip install yt-dlp")
        return
    dest_dir = svc.settings.backgrounds_dir / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    info = await context.bot.send_message(chat_id, f"Téléchargement dans « {category} »… Cela peut prendre quelques minutes.")
    proc = await asyncio.create_subprocess_exec(
        exe, "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4", "--no-playlist", "--restrict-filenames",
        "-o", str(dest_dir / "%(title).60s-%(id)s.%(ext)s"), "--print", "after_move:filepath",
        "--quiet", "--no-warnings", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        await info.edit_text(f"✕ Échec du téléchargement : {err.decode('utf-8', 'replace')[-300:]}")
        return
    path = Path(out.decode().strip().splitlines()[-1]) if out.strip() else None
    dur = await svc.pipeline.bank.duration(path) if path and path.exists() else 0
    await info.edit_text(f"✓ Ajoutée : {path.name if path else url}, {int(dur // 60)} min",
                         reply_markup=kb([[("Banque vidéo", "bk")]]))
    await db.log_event(f"Nouveau fond dans « {category} » : {path.name if path else url}")


# ---------------------------------------------------------------------------- erreurs
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Erreur Telegram", exc_info=context.error)
    if isinstance(update, Update) and update.callback_query:
        try:
            await update.callback_query.answer(str(context.error)[:190], show_alert=True)
        except TelegramError:
            pass


def register(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler(["start", "dashboard", "menu"], cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("channels", screen_cmd(screens.channel_list)))
    app.add_handler(CommandHandler("generate", screen_cmd(screens.generate_picker)))
    app.add_handler(CommandHandler("queue", screen_cmd(screens.queue)))
    app.add_handler(CommandHandler("stats", screen_cmd(screens.stats)))
    app.add_handler(CommandHandler("bank", screen_cmd(screens.bank)))
    app.add_handler(CommandHandler("settings", screen_cmd(screens.settings_screen)))
    app.add_handler(CommandHandler("log", screen_cmd(screens.events)))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(private & (filters.VIDEO | filters.Document.ALL), on_file))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
