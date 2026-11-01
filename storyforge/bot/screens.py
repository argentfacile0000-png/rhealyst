"""Écrans du dashboard Telegram : chaque fonction renvoie (texte HTML, clavier)."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from datetime import timezone

from sqlalchemy import func, select
from telegram import InlineKeyboardMarkup

from .. import db
from ..models import Channel, Publication, PubStatus, Story, Video, VideoStatus
from ..stats import human_delta, overview, tz
from ..story.styles import FORMATS, get_language
from .fields import FIELD_MAP, GROUPS, fields_in
from .ui import PLATFORM, PUB_STATUS, SEP, VIDEO_STATUS, bar, esc, kb, kfmt, mmss, short, spark, webapp_button

if TYPE_CHECKING:
    from ..core import Services

Screen = tuple[str, InlineKeyboardMarkup]


# ---------------------------------------------------------------------------- accueil
async def home(svc: Services) -> Screen:
    o = await overview(svc, recent_limit=0)
    t = o["today"]
    state = "⏸ <b>EN PAUSE</b>" if o["paused"] else "🟢 <b>En ligne</b>"
    lines = [
        "🎬 <b>STORYFORGE</b> · <i>Control Center</i>",
        SEP,
        f"{state} · ⏱ {o['uptime']}",
        "",
        "📊 <b>Aujourd'hui</b>",
        f"├ 🎥 Générées : <b>{t['generated']}</b>",
        f"├ 🚀 Publiées : <b>{t['published']}</b>  <i>(YT {t['youtube']} · TT {t['tiktok']})</i>",
        f"├ 🟡 À valider : <b>{o['pending_approval']}</b> · 🟢 Prêtes : <b>{o['ready']}</b>",
        f"└ ❌ Échecs : <b>{t['failed']}</b>",
    ]
    running = [j for j in o["jobs"] if j["status"] == "running"]
    if o["jobs"]:
        lines += ["", f"⚙️ <b>En cours</b> ({len(running)} actif · {len(o['jobs']) - len(running)} en attente)"]
        for j in running[:3]:
            lines.append(f"└ {esc(j['label'])} <i>({mmss(j['since'])})</i>")

    chs = o["channels"]
    lines += ["", f"📺 <b>Chaînes</b> ({sum(c['active'] for c in chs)} actives / {len(chs)})"]
    if not chs:
        lines.append("<i>Aucune chaîne — crée la première avec ➕</i>")
    for c in chs[:12]:
        dot = "🟢" if c["active"] else "⏸"
        plat = ("▶️" if c["youtube"]["linked"] and c["youtube"]["enabled"] else "") + (
            "🎵" if c["tiktok"]["linked"] and c["tiktok"]["enabled"] else "")
        nxt = f" · ⏰ {c['next_slot']}" if c["active"] else ""
        lines.append(f"{dot} {c['flag']} <b>{esc(short(c['name'], 26))}</b> {plat}  <i>{c['per_day']}/j{nxt}</i>")

    q = o["quota"]
    if q and q.get("limit"):
        frac = q["used"] / q["limit"]
        lines += ["", f"🔋 ElevenLabs {bar(1 - frac)} {kfmt(q['limit'] - q['used'])} car. restants"]

    rows = [
        [("📺 Chaînes", "cl"), ("➕ Nouvelle chaîne", "new")],
        [("⚡ Générer", "gen"), ("📋 File & planning", "q")],
        [("📈 Statistiques", "st"), ("🎞 Banque vidéos", "bk")],
        [("📜 Journal", "ev"), ("⚙️ Réglages", "set")],
        [("▶️ Reprendre" if o["paused"] else "⏸ Pause globale", "sp"), ("🔄 Actualiser", "h")],
    ]
    url = svc.settings.webapp_url
    if url:
        rows.insert(0, [webapp_button("🖥 Ouvrir le dashboard visuel", url)])
    return "\n".join(lines), kb(rows)


# ---------------------------------------------------------------------------- chaînes
async def channel_list(svc: Services) -> Screen:
    o = await overview(svc, recent_limit=0)
    lines = ["📺 <b>Tes chaînes</b>", SEP]
    rows = []
    for c in o["channels"]:
        dot = "🟢" if c["active"] else "⏸"
        lines.append(
            f"{dot} {c['flag']} <b>{esc(c['name'])}</b>\n"
            f"    🟢 {c['ready']} prêtes · 🟡 {c['pending']} à valider · ⏰ {c['next_slot'] if c['active'] else 'inactive'}"
        )
        rows.append([(f"{dot} {short(c['name'], 30)}", f"cv:{c['id']}")])
    if not o["channels"]:
        lines.append("<i>Aucune chaîne pour l'instant.</i>")
    rows.append([("➕ Nouvelle chaîne", "new"), ("⬅️ Accueil", "h")])
    return "\n".join(lines), kb(rows)


async def generate_picker(svc: Services) -> Screen:
    chs = await svc.list_channels()
    rows = [[(f"⚡ {short(c.name, 32)}", f"cg:{c.id}")] for c in chs]
    rows.append([("⬅️ Accueil", "h")])
    text = "⚡ <b>Générer maintenant</b>\n" + SEP + "\nChoisis la chaîne :" if chs else "Crée d'abord une chaîne ➕"
    return text, kb(rows)


async def channel_view(svc: Services, cid: int) -> Screen:
    ch = await svc.get_channel(cid)
    if ch is None:
        return "Chaîne introuvable.", kb([[("⬅️ Chaînes", "cl")]])
    zone = tz(svc)
    lang = get_language(ch.language)
    async with db.session() as s:
        counts = dict((await s.execute(
            select(Video.status, func.count()).where(Video.channel_id == cid).group_by(Video.status)
        )).all())
        last = (await s.execute(
            select(Video).where(Video.channel_id == cid).order_by(Video.id.desc()).limit(1)
        )).scalar_one_or_none()
        published_total = (await s.execute(
            select(func.count()).select_from(Publication).where(
                Publication.channel_id == cid, Publication.status.in_(PubStatus.OK))
        )).scalar_one()
    nxt = svc.scheduler.next_run(cid)
    fmts = " ".join(FORMATS[f].emoji for f in ch.formats if f in FORMATS) or "défaut"

    def plat(enabled: bool, linked: bool, account: str) -> str:
        if not enabled:
            return "⏸ désactivé"
        return f"✅ {esc(account) or 'lié'}" if linked else "⚠️ non lié"

    lines = [
        f"{'🟢' if ch.active else '⏸'} <b>{esc(ch.name)}</b>  {lang.flag}",
        SEP,
        f"📚 Formats : {fmts}",
        f"🗣 Voix : <b>{esc(ch.voice_name or ch.voice_id or 'défaut')}</b> · {ch.voice_speed}× → {ch.speedup}×",
        f"🎞 Fonds : {esc(', '.join(ch.bg_categories) or 'toutes catégories')}",
        f"🎯 Durée : {ch.min_seconds}-{ch.max_seconds} s · {ch.parts} partie{'s' if ch.parts > 1 else ''}",
        f"⏰ {' · '.join(ch.schedule_times) or 'aucun créneau'} <i>({esc(ch.timezone)})</i>",
        f"✋ Validation manuelle : {'ON' if ch.approval_required else 'OFF'} · réserve {ch.buffer_size}",
        "",
        "🔗 <b>Plateformes</b>",
        f"├ ▶️ YouTube : {plat(ch.youtube_enabled, ch.youtube_linked, ch.youtube_account)}",
        f"└ 🎵 TikTok : {plat(ch.tiktok_enabled, ch.tiktok_linked, ch.tiktok_account)}",
        "",
        f"📦 🟢 {counts.get(VideoStatus.READY, 0)} prêtes · 🟡 {counts.get(VideoStatus.PENDING_APPROVAL, 0)} à valider"
        f" · 🚀 {published_total} publications",
        f"⏭ Prochain créneau : <b>{human_delta(nxt.astimezone(timezone.utc).replace(tzinfo=None), zone) if nxt else '—'}</b>",
    ]
    if last:
        icon, label = VIDEO_STATUS.get(last.status, ("•", last.status))
        lines.append(f"🕐 Dernière : {icon} « {esc(short(last.title, 40))} » {human_delta(last.created_at, zone)}")
    if not ch.platforms():
        lines += ["", "💡 <i>Lie YouTube et/ou TikTok pour publier automatiquement.</i>"]

    rows = [
        [("⏸ Mettre en pause" if ch.active else "▶️ Activer", f"ct:{cid}"), ("⚡ Générer", f"cg:{cid}")],
        [("✏️ Réglages", f"ce:{cid}"), ("🎬 Vidéos", f"cvv:{cid}:0")],
        [("🔗 YouTube" if not ch.youtube_linked else "♻️ YouTube", f"cy:{cid}"),
         ("🔗 TikTok" if not ch.tiktok_linked else "♻️ TikTok", f"ck:{cid}")],
        [("🚀 Publier la prochaine", f"cp:{cid}"), ("🗑 Supprimer", f"cd:{cid}")],
        [("⬅️ Chaînes", "cl"), ("🏠 Accueil", "h")],
    ]
    return "\n".join(lines), kb(rows)


async def channel_edit(svc: Services, cid: int) -> Screen:
    ch = await svc.get_channel(cid)
    if ch is None:
        return "Chaîne introuvable.", kb([[("⬅️ Chaînes", "cl")]])
    lines = [f"✏️ <b>Réglages · {esc(ch.name)}</b>", SEP, "Choisis une catégorie :"]
    rows = [[(label, f"cE:{cid}:{key}")] for key, label in GROUPS]
    rows.append([("⬅️ Retour", f"cv:{cid}")])
    return "\n".join(lines), kb(rows)


async def channel_group(svc: Services, cid: int, group: str) -> Screen:
    ch = await svc.get_channel(cid)
    if ch is None:
        return "Chaîne introuvable.", kb([[("⬅️ Chaînes", "cl")]])
    label = dict(GROUPS).get(group, group)
    lines = [f"{label} · <b>{esc(ch.name)}</b>", SEP]
    rows = []
    for f in fields_in(group):
        val = f.value_text(ch)
        lines.append(f"• {f.label} : <b>{esc(val)}</b>")
        rows.append([(f"{f.label} · {short(val, 22)}", f"cf:{cid}:{f.key}")])
    rows.append([("⬅️ Catégories", f"ce:{cid}"), ("📺 Chaîne", f"cv:{cid}")])
    return "\n".join(lines), kb(rows)


async def field_choices(svc: Services, cid: int, key: str, bg_categories: list[str] | None = None) -> Screen:
    ch = await svc.get_channel(cid)
    f = FIELD_MAP[key]
    lines = [f"⚙️ <b>{esc(f.label)}</b> · {esc(ch.name)}", SEP]
    rows = []
    if f.kind == "multi":
        if key == "bg_categories":
            opts = [(c, c) for c in (bg_categories or [])]
            current = set(ch.bg_categories or [])
            lines.append("Coche les catégories de fond à utiliser (aucune = toutes) :")
        else:
            opts = f.options
            current = set(getattr(ch, key) or [])
            lines.append("Coche les formats à alterner (tirés au hasard à chaque histoire) :")
            for label, v in opts:
                fmt = FORMATS.get(v)
                if fmt:
                    lines.append(f"\n{label}\n<i>{esc(short(fmt.brief, 140))}</i>")
        for i, (label, v) in enumerate(opts):
            rows.append([(f"{'✅' if v in current else '⬜'} {label}", f"cm:{cid}:{key}:{i}")])
        if not opts:
            lines.append("<i>Aucune catégorie dans la banque vidéo.</i>")
    else:
        cur = f.current(ch) if f.current else getattr(ch, key)
        lines.append("Choisis une valeur :")
        row: list = []
        for i, (label, v) in enumerate(f.options):
            row.append((f"{'🔘' if v == cur else '⚪'} {label}", f"co:{cid}:{key}:{i}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([("⬅️ Retour", f"cE:{cid}:{f.group}")])
    return "\n".join(lines), kb(rows)


async def voice_picker(svc: Services, cid: int, voices: list[dict], page: int) -> Screen:
    ch = await svc.get_channel(cid)
    per = 8
    pages = max(1, (len(voices) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    lines = [f"🗣 <b>Voix ElevenLabs</b> · {esc(ch.name)}", SEP,
             f"Actuelle : <b>{esc(ch.voice_name or ch.voice_id)}</b>", f"<i>Page {page + 1}/{pages}</i>"]
    rows = []
    for i, v in enumerate(voices[page * per:(page + 1) * per], start=page * per):
        tags = " · ".join(x for x in (v.get("gender"), v.get("accent"), v.get("age")) if x)
        mark = "🔘" if v["voice_id"] == ch.voice_id else "⚪"
        rows.append([(f"{mark} {short(v['name'], 22)} {('· ' + tags) if tags else ''}"[:60], f"vsel:{cid}:{i}")])
    nav = []
    if page > 0:
        nav.append(("◀️", f"vp:{cid}:{page - 1}"))
    if page < pages - 1:
        nav.append(("▶️", f"vp:{cid}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("⌨️ Coller un ID de voix", f"vid:{cid}")])
    rows.append([("⬅️ Retour", f"cE:{cid}:voice")])
    return "\n".join(lines), kb(rows)


# ---------------------------------------------------------------------------- vidéos
async def channel_videos(svc: Services, cid: int, page: int = 0) -> Screen:
    ch = await svc.get_channel(cid)
    zone = tz(svc)
    per = 8
    async with db.session() as s:
        vids = list((await s.execute(
            select(Video).where(Video.channel_id == cid).order_by(Video.id.desc()).offset(page * per).limit(per + 1)
        )).scalars().all())
    more = len(vids) > per
    vids = vids[:per]
    lines = [f"🎬 <b>Vidéos · {esc(ch.name if ch else '?')}</b>", SEP]
    rows = []
    for v in vids:
        icon, label = VIDEO_STATUS.get(v.status, ("•", v.status))
        part = f" [{v.part_index}/{v.part_count}]" if v.part_count > 1 else ""
        lines.append(f"{icon} <b>#{v.id}</b>{part} {esc(short(v.title, 38))} · <i>{human_delta(v.created_at, zone)}</i>")
        rows.append([(f"{icon} #{v.id} {short(v.title, 30)}", f"v:{v.id}")])
    if not vids:
        lines.append("<i>Aucune vidéo pour l'instant.</i>")
    nav = []
    if page > 0:
        nav.append(("◀️ Récentes", f"cvv:{cid}:{page - 1}"))
    if more:
        nav.append(("Anciennes ▶️", f"cvv:{cid}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("⬅️ Chaîne", f"cv:{cid}")])
    return "\n".join(lines), kb(rows)


async def video_caption(svc: Services, video_id: int, *, with_text: bool = False) -> str:
    zone = tz(svc)
    async with db.session() as s:
        v = await s.get(Video, video_id)
        if v is None:
            return "Vidéo introuvable."
        ch = await s.get(Channel, v.channel_id)
        story = await s.get(Story, v.story_id)
        pubs = list((await s.execute(select(Publication).where(Publication.video_id == video_id))).scalars())
    icon, label = VIDEO_STATUS.get(v.status, ("•", v.status))
    fmt = FORMATS.get(story.format) if story else None
    part = f" · Partie {v.part_index}/{v.part_count}" if v.part_count > 1 else ""
    lines = [
        f"{icon} <b>{label}</b> · #{v.id} · {esc(ch.name if ch else '?')}",
        f"<b>« {esc(short(v.title, 120))} »</b>{part}",
        f"{fmt.emoji + ' ' + fmt.label if fmt else ''} · ⏱ {mmss(v.duration)} · {human_delta(v.created_at, zone)}",
    ]
    if v.bg_file:
        lines.append(f"🎞 <i>{esc(short(v.bg_file.split('/')[-1], 40))} @ {mmss(v.bg_start)}</i>")
    for p in pubs:
        status = PUB_STATUS.get(p.status, "•")
        line = f"{PLATFORM.get(p.platform, p.platform)} {status}"
        if p.url:
            line += f" <a href=\"{esc(p.url)}\">voir</a>"
        if p.status == PubStatus.DRAFT:
            line += " <i>(à finaliser dans l'app TikTok)</i>"
        if p.error:
            line += f" <i>{esc(short(p.error, 90))}</i>"
        lines.append(line)
    if v.error and v.status == VideoStatus.FAILED:
        lines.append(f"⚠️ <i>{esc(short(v.error, 200))}</i>")
    if with_text:
        lines += ["", f"<blockquote expandable>{esc(v.text[:2800])}</blockquote>"]
    else:
        lines.append(f"\n<i>{esc(short(v.text, 220))}</i>")
    return "\n".join(lines)


def video_keyboard(video: Video, *, nav: bool = True) -> InlineKeyboardMarkup:
    vid = video.id
    rows: list[list] = []
    if video.status == VideoStatus.PENDING_APPROVAL:
        rows.append([("✅ Valider", f"va:{vid}"), ("🚀 Publier maintenant", f"vn:{vid}")])
        rows.append([("🔁 Régénérer", f"vr:{vid}"), ("🗑 Jeter", f"vx:{vid}")])
    elif video.status == VideoStatus.READY:
        rows.append([("🚀 Publier maintenant", f"vn:{vid}"), ("🗑 Jeter", f"vx:{vid}")])
    elif video.status in (VideoStatus.FAILED, VideoStatus.PUBLISHED) and video.file_path:
        rows.append([("🔁 Republier les échecs", f"vn:{vid}"), ("🗑 Jeter", f"vx:{vid}")])
    extra = [("📜 Texte complet", f"vt:{vid}")]
    if video.file_path or video.preview_path:
        extra.insert(0, ("👁 Aperçu vidéo", f"vf:{vid}"))
    rows.append(extra)
    if nav:
        rows.append([("📺 Chaîne", f"cv:{video.channel_id}"), ("🏠 Accueil", "h")])
    return kb(rows)


async def video_view(svc: Services, video_id: int, *, with_text: bool = False) -> Screen:
    v = await svc.get_video(video_id)
    if v is None:
        return "Vidéo introuvable.", kb([[("🏠 Accueil", "h")]])
    return await video_caption(svc, video_id, with_text=with_text), video_keyboard(v)


# ---------------------------------------------------------------------------- file & planning
async def queue(svc: Services) -> Screen:
    zone = tz(svc)
    jobs = svc.jobs.snapshot()
    chs = {c.id: c for c in await svc.list_channels()}
    lines = ["📋 <b>File d'attente & planning</b>", SEP]
    running = [j for j in jobs if j.status == "running"]
    pending = [j for j in jobs if j.status != "running"]
    lines.append(f"⚙️ <b>En cours</b> ({len(running)})")
    lines += [f"└ {esc(j.label)}" for j in running] or ["└ <i>rien</i>"]
    if pending:
        lines.append(f"\n⏳ <b>En attente</b> ({len(pending)})")
        lines += [f"└ {esc(j.label)}" for j in pending[:8]]

    async with db.session() as s:
        waiting = list((await s.execute(
            select(Video).where(Video.status.in_([VideoStatus.PENDING_APPROVAL, VideoStatus.READY]))
            .order_by(Video.channel_id, Video.story_id, Video.part_index).limit(20)
        )).scalars().all())
    rows = []
    if waiting:
        lines.append(f"\n📦 <b>Vidéos en stock</b> ({len(waiting)})")
        for v in waiting:
            icon, _ = VIDEO_STATUS.get(v.status, ("•", ""))
            name = chs[v.channel_id].name if v.channel_id in chs else "?"
            lines.append(f"{icon} #{v.id} <b>{esc(short(name, 18))}</b> · {esc(short(v.title, 34))}")
            if len(rows) < 8:
                rows.append([(f"{icon} #{v.id} · {short(v.title, 34)}", f"v:{v.id}")])

    upcoming = svc.scheduler.upcoming(8)
    if upcoming:
        lines.append("\n🕒 <b>Prochains créneaux</b>")
        for when, cid in upcoming:
            name = chs[cid].name if cid in chs else f"#{cid}"
            lines.append(f"└ {when.astimezone(zone):%a %d/%m %H:%M} · {esc(short(name, 28))}")
    rows.append([("🔄 Actualiser", "q"), ("🏠 Accueil", "h")])
    return "\n".join(lines), kb(rows)


# ---------------------------------------------------------------------------- stats
async def stats(svc: Services) -> Screen:
    o = await overview(svc, recent_limit=0, days=14)
    ser = o["series"]
    hi = max(ser["published"] + [1])
    lines = ["📈 <b>Statistiques · 14 jours</b>", SEP, "<code>"]
    for label, p, g in list(zip(ser["labels"], ser["published"], ser["generated"]))[-10:]:
        lines.append(f"{label} {'█' * int(p / hi * 14):<14} {p:>2} 🚀 {g:>2} 🎥")
    lines.append("</code>")
    total_p, total_g = sum(ser["published"]), sum(ser["generated"])
    lines += [
        f"Tendance publications : <code>{spark(ser['published'])}</code>",
        f"🚀 <b>{total_p}</b> publications · 🎥 <b>{total_g}</b> vidéos générées",
        "",
        "📺 <b>Aujourd'hui par chaîne</b>",
    ]
    for c in o["channels"]:
        lines.append(f"{c['flag']} {esc(short(c['name'], 24))} : <b>{c['today']}</b> / {c['per_day']} prévues")
    t = o["today"]
    lines += ["", "💳 <b>Consommation du jour</b>",
              f"🧠 OpenAI : {kfmt(t['openai_tokens'])} tokens", f"🎙 ElevenLabs : {kfmt(t['tts_chars'])} caractères"]
    q = o["quota"]
    if q and q.get("limit"):
        lines.append(f"🔋 Forfait {esc(q.get('tier', ''))} : {bar(q['used'] / q['limit'])} {kfmt(q['used'])}/{kfmt(q['limit'])}")
    return "\n".join(lines), kb([[("🔄 Actualiser", "st"), ("🏠 Accueil", "h")]])


# ---------------------------------------------------------------------------- banque vidéo
async def bank(svc: Services) -> Screen:
    summary = await svc.pipeline.bank.summary()
    lines = ["🎞 <b>Banque de vidéos de fond</b>", SEP,
             "Chaque vidéo est tirée <b>au hasard</b>, puis une <b>séquence au hasard</b> de la durée exacte "
             "de la narration (ex : 1:38 prise n'importe où dans un fond de 43 min).", ""]
    if not summary:
        lines.append("<i>Banque vide.</i> Dépose des .mp4 dans <code>data/backgrounds/&lt;catégorie&gt;/</code> "
                     "ou ajoute-les ici.")
    total = 0.0
    for cat, info in sorted(summary.items()):
        total += info["seconds"]
        lines.append(f"📁 <b>{esc(cat)}</b> · {info['files']} vidéo(s) · {mmss(info['seconds'])}")
    if summary:
        lines.append(f"\n⏱ Total : <b>{int(total // 3600)} h {int(total % 3600 // 60):02d}</b> de footage")
    ytdlp = "✅" if shutil.which("yt-dlp") else "❌ (pip install yt-dlp)"
    lines.append(f"\n🔗 Ajout par lien (yt-dlp) : {ytdlp}")
    return "\n".join(lines), kb([[("➕ Ajouter une vidéo", "bka"), ("🔄 Rescanner", "bk")], [("🏠 Accueil", "h")]])


async def bank_categories(svc: Services) -> Screen:
    cats = sorted(svc.pipeline.bank.categories().keys())
    rows = [[(f"📁 {c}", f"bkc:{i}")] for i, c in enumerate(cats)]
    rows.append([("✏️ Nouvelle catégorie", "bkn")])
    rows.append([("⬅️ Banque", "bk")])
    return "🎞 <b>Ajouter une vidéo</b>\n" + SEP + "\nDans quelle catégorie ?", kb(rows)


# ---------------------------------------------------------------------------- réglages / journal
async def settings_screen(svc: Services) -> Screen:
    s = svc.settings
    paused = await svc.is_paused()
    live = bool(await db.kv_get("live_auto", False))
    lines = [
        "⚙️ <b>Réglages système</b>", SEP,
        f"🧠 Modèle OpenAI : <code>{esc(s.openai_model)}</code>",
        f"🎙 Modèle ElevenLabs : <code>{esc(s.elevenlabs_model)}</code>",
        f"🎬 Rendus simultanés : {s.max_concurrent_renders} · {s.video_width}×{s.video_height} · CRF {s.video_crf}",
        f"🧹 Conservation des fichiers : {s.keep_videos_days} jours",
        f"🌍 Fuseau : {esc(s.timezone)}",
        f"🖥 Mini App : {'✅ ' + esc(s.webapp_url) if s.webapp_url else '❌ (WEBAPP_ENABLED + PUBLIC_URL https)'}",
        "",
        f"{'⏸' if paused else '🟢'} Système : <b>{'en pause' if paused else 'actif'}</b>",
        f"📡 Suivi live des générations auto : <b>{'ON' if live else 'OFF'}</b>",
    ]
    rows = [
        [("▶️ Reprendre" if paused else "⏸ Pause globale", "sp"), ("📡 Suivi live", "slv")],
        [("🩺 Tester les APIs", "stt"), ("📜 Journal", "ev")],
        [("🏠 Accueil", "h")],
    ]
    return "\n".join(lines), kb(rows)


async def events(svc: Services) -> Screen:
    zone = tz(svc)
    evs = await db.recent_events(20)
    icons = {"info": "▫️", "success": "✅", "warning": "⚠️", "error": "❌"}
    lines = ["📜 <b>Journal d'activité</b>", SEP]
    for e in evs:
        lines.append(f"{icons.get(e.level, '▫️')} <i>{human_delta(e.ts, zone)}</i> — {esc(short(e.text, 140))}")
    if not evs:
        lines.append("<i>Rien pour l'instant.</i>")
    return "\n".join(lines)[:4000], kb([[("🔄 Actualiser", "ev"), ("🏠 Accueil", "h")]])
