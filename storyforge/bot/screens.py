"""Écrans du dashboard Telegram (texte HTML + clavier), style Minimal Design System."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from telegram import InlineKeyboardMarkup

from .. import db
from ..models import Channel, Publication, PubStatus, Story, Video, VideoFormat, VideoStatus
from ..stats import human_delta, overview, to_local, tz
from ..story.styles import FORMATS, get_language
from ..version import auto_update_flag, deployed_version
from .fields import FIELD_MAP, GROUPS, fields_in, plural_tracks
from .ui import (
    BACK,
    CHECK,
    CROSS,
    DOT,
    HOME,
    OFF,
    ON,
    PLATFORM,
    PUB_STATUS,
    VIDEO_STATUS,
    bar,
    esc,
    hhmm,
    kb,
    kfmt,
    mmss,
    muted,
    nav,
    num,
    plural,
    row,
    short,
    strip_platform,
    title,
    webapp_button,
)

if TYPE_CHECKING:
    from ..core import Services

Screen = tuple[str, InlineKeyboardMarkup]


def _utc(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt else None


# ---------------------------------------------------------------------------- accueil
async def home(svc: Services) -> Screen:
    o = await overview(svc, recent_limit=0)
    t = o["today"]
    lines = [
        "<b>StoryForge</b>",
        muted(f"{OFF} En pause · en ligne depuis {o['uptime']}" if o["paused"]
              else f"{ON} Actif · en ligne depuis {o['uptime']}"),
        "",
        "<b>Aujourd'hui</b>",
        row("Générées", t["generated"]),
        row("Publiées", t["published"]) + "  " + muted(f"YouTube {t['youtube']}, TikTok {t['tiktok']}"),
        row("À valider", o["pending_approval"]),
        row("Échecs", t["failed"]),
    ]

    running = [j for j in o["jobs"] if j["status"] == "running"]
    waiting = len(o["jobs"]) - len(running)
    if o["jobs"]:
        lines += ["", "<b>En cours</b>"]
        lines += [f"{esc(j['label'])}  {muted(mmss(j['since']))}" for j in running[:3]]
        if waiting:
            lines.append(muted(f"{waiting} en attente"))

    chs = o["channels"]
    active = sum(c["active"] for c in chs)
    actives = f"{active} active{'s' if active > 1 else ''} sur {len(chs)}"
    lines += ["", "<b>Chaînes</b>  " + muted(actives)]
    if not chs:
        lines.append(muted("Aucune chaîne. Crée la première pour commencer."))
    for c in chs[:12]:
        detail = f"{c['per_day']}/jour{DOT}prochain {c['next_slot']}" if c["active"] else "en pause"
        lines.append(f"{ON if c['active'] else OFF} {esc(short(c['name'], 28))}  {muted(detail)}")

    q = o["quota"]
    if q and q.get("limit"):
        left = max(0, q["limit"] - q["used"])
        credit = f"{num(left)} restants sur {num(q['limit'])}"
        lines += ["", "<b>Crédits ElevenLabs</b>  " + muted(credit), f"<code>{bar(left / q['limit'], 16)}</code>"]

    rows = [
        [("Chaînes", "cl"), ("Nouvelle chaîne", "new")],
        [("Générer", "gen"), ("File d'attente", "q")],
        [("Banque vidéo", "bk"), ("Bande son", "mu")],
        [("Statistiques", "st"), ("Journal", "ev")],
        [("Réglages", "set"), ("Reprendre" if o["paused"] else "Pause globale", "sp")],
        [("Actualiser", "h")],
    ]
    url = svc.settings.webapp_url
    if url:
        rows.insert(0, [webapp_button("Ouvrir le dashboard", url)])
    return "\n".join(lines), kb(rows)


# ---------------------------------------------------------------------------- chaînes
async def channel_list(svc: Services) -> Screen:
    o = await overview(svc, recent_limit=0)
    chs = o["channels"]
    active = sum(c["active"] for c in chs)
    lines = [title("Chaînes"), muted(f"{active} active{'s' if active > 1 else ''} sur {len(chs)}"), ""]
    rows = []
    for c in chs:
        stock = [plural(c["ready"], "prête")]
        if c["pending"]:
            stock.append(f"{c['pending']} à valider")
        stock.append(f"prochain {c['next_slot']}" if c["active"] else "en pause")
        lines.append(f"{ON if c['active'] else OFF} <b>{esc(c['name'])}</b>  {muted(c['language'].upper())}")
        lines.append(muted(DOT.join(stock)))
        lines.append("")
        rows.append([(f"{ON if c['active'] else OFF} {short(c['name'], 34)}", f"cv:{c['id']}")])
    if not chs:
        lines.append(muted("Aucune chaîne pour l'instant."))
    rows.append([("Nouvelle chaîne", "new"), (HOME, "h")])
    return "\n".join(lines).rstrip(), kb(rows)


async def generate_kind(svc: Services, cid: int) -> Screen:
    ch = await svc.get_channel(cid)
    if ch is None:
        return "Chaîne introuvable.", kb([nav("cl")])
    lines = [title("Générer", ch.name), "", "Quel format ?", "",
             row("Short", f"vertical, {ch.min_seconds}–{ch.max_seconds} s") + "  " + muted("YouTube, TikTok, Instagram"),
             row("Vidéo longue", f"16:9, {ch.long_min_minutes}–{ch.long_max_minutes} min") + "  " + muted("YouTube")]
    rows = [[("Short", f"cg:{cid}:vertical"), ("Vidéo longue", f"cg:{cid}:horizontal")], [(BACK, f"cv:{cid}")]]
    return "\n".join(lines), kb(rows)


async def generate_picker(svc: Services) -> Screen:
    chs = await svc.list_channels()
    if not chs:
        return f"{title('Générer')}\n\n{muted('Crée d’abord une chaîne.')}", kb([[("Nouvelle chaîne", "new"), (HOME, "h")]])
    rows = [[(short(c.name, 36), f"cg:{c.id}")] for c in chs]
    rows.append(nav())
    return f"{title('Générer')}\n\nPour quelle chaîne ?", kb(rows)


def _formats_label(ch: Channel) -> str:
    return ", ".join(FORMATS[f].label for f in ch.formats if f in FORMATS) or "par défaut"


async def channel_view(svc: Services, cid: int) -> Screen:
    ch = await svc.get_channel(cid)
    if ch is None:
        return "Chaîne introuvable.", kb([nav("cl")])
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

    def plat(enabled: bool, linked: bool, account: str) -> str:
        if not enabled:
            return muted("désactivé")
        return f"{CHECK} <b>{esc(account or 'lié')}</b>" if linked else muted("non lié")

    lines = [
        f"<b>{esc(ch.name)}</b>",
        muted(f"{ON} Active" if ch.active else f"{OFF} En pause") + f"  {muted(lang.label)}",
        "",
        "<b>Contenu</b>",
        row("Histoires", _formats_label(ch)),
        row("Shorts", f"{ch.min_seconds}–{ch.max_seconds} s, {plural(ch.parts, 'partie')}" if ch.shorts_enabled
            else "désactivés"),
        row("Vidéos longues", f"{ch.long_min_minutes}–{ch.long_max_minutes} min, YouTube" if ch.long_enabled
            else "désactivées"),
        row("Voix", f"{ch.voice_name or ch.voice_id or 'par défaut'}"),
        row("Fonds", ", ".join(ch.bg_categories) or "toutes catégories"),
        row("Bande son", f"{round(ch.music_volume * 100)} %, {plural_tracks(ch.music_tracks)}" if ch.music_volume > 0
            else "aucune"),
        "",
        "<b>Planning</b>",
        row("Créneaux", ", ".join(ch.schedule_times) or "aucun") + f"  {muted(ch.timezone)}",
        *([row("Créneaux vidéos longues", ", ".join(ch.long_schedule_times or []) or "aucun")] if ch.long_enabled else []),
        row("Prochain", human_delta(_utc(nxt), zone) if nxt else "—"),
        row("Validation manuelle", "activée" if ch.approval_required else "désactivée"),
        row("Réserve", plural(ch.buffer_size, "vidéo")),
        "",
        "<b>Plateformes</b>",
        f"YouTube  {plat(ch.youtube_enabled, ch.youtube_linked, ch.youtube_account)}",
        f"TikTok  {plat(ch.tiktok_enabled, ch.tiktok_linked, ch.tiktok_account)}",
        f"Instagram  {plat(ch.instagram_enabled, ch.instagram_linked, ch.instagram_account)}",
        "",
        "<b>Stock</b>",
        f"{plural(counts.get(VideoStatus.READY, 0), 'prête')}{DOT}"
        f"{counts.get(VideoStatus.PENDING_APPROVAL, 0)} à valider{DOT}"
        f"{plural(published_total, 'publication')}",
    ]
    if last:
        _, label = VIDEO_STATUS.get(last.status, ("", last.status))
        lines.append(muted(f"Dernière : « {short(last.title, 40)} », {label.lower()}, {human_delta(last.created_at, zone)}"))
    if not ch.platforms():
        lines += ["", muted("Connecte YouTube, TikTok ou Instagram dans Comptes pour publier automatiquement.")]

    rows = [
        [("Mettre en pause" if ch.active else "Activer", f"ct:{cid}"), ("Générer", f"cg:{cid}")],
        [("Réglages", f"ce:{cid}"), ("Vidéos", f"cvv:{cid}:0"), ("Archives", f"cva:{cid}:0")],
        [("Comptes", f"ca:{cid}"), ("Publier la suivante", f"cp:{cid}")],
        [("Supprimer", f"cd:{cid}")],
        nav("cl", "← Chaînes"),
    ]
    return "\n".join(lines), kb(rows)


async def accounts(svc: Services, cid: int) -> Screen:
    ch = await svc.get_channel(cid)
    if ch is None:
        return "Chaîne introuvable.", kb([nav("cl")])

    def state(platform: str, linked: bool, account: str, enabled: bool) -> str:
        if linked:
            return f"{CHECK} <b>{esc(account or 'connecté')}</b>" + ("" if enabled else "  " + muted("publication désactivée"))
        problem = svc.platform_problem(platform, cid)
        return muted("à configurer : Réglages, Plateformes" if problem else "non connecté")

    lines = [
        title("Comptes", ch.name),
        "",
        f"YouTube  {state('youtube', ch.youtube_linked, ch.youtube_account, ch.youtube_enabled)}",
        f"TikTok  {state('tiktok', ch.tiktok_linked, ch.tiktok_account, ch.tiktok_enabled)}",
        f"Instagram  {state('instagram', ch.instagram_linked, ch.instagram_account, ch.instagram_enabled)}",
        "",
    ]
    if ch.tiktok_linked and not ch.tiktok_post:
        lines += [f"{CROSS} TikTok : choisis tes <b>réglages de publication</b> (visibilité, interactions) sur la "
                  "page de connexion, sinon les vidéos partent en brouillon ou ne sont pas publiées.", ""]
    rows: list[list] = []
    if svc.portal_available():
        lines.append("Ouvre la page de connexion, choisis la plateforme et accepte les autorisations.")
        lines.append(muted("Le lien est personnel et expire dans 30 minutes."))
        rows.append([("Ouvrir la page de connexion", svc.portal_link(cid))])
    else:
        lines.append("Choisis la plateforme à connecter. Après l'autorisation, colle ici l'adresse de la page de retour.")
        lines.append(muted("Pour une connexion en un clic, configure PUBLIC_URL et WEBAPP_ENABLED (voir le README)."))
        rows.append([("Connecter YouTube", f"cy:{cid}"), ("Connecter TikTok", f"ck:{cid}")])
        rows.append([("Connecter Instagram", f"ci:{cid}")])
    if any(svc.platform_problem(p, cid) for p in ("youtube", "tiktok", "instagram")):
        rows.append([("Configurer les plateformes", "pf")])
    unlink = [(f"Déconnecter {PLATFORM[p]}", f"cu:{cid}:{p}")
              for p in ("youtube", "tiktok", "instagram") if getattr(ch, f"{p}_linked")]
    for i in range(0, len(unlink), 2):
        rows.append(unlink[i:i + 2])
    rows.append([("← Chaîne", f"cv:{cid}"), (HOME, "h")])
    return "\n".join(lines), kb(rows)


async def channel_edit(svc: Services, cid: int) -> Screen:
    ch = await svc.get_channel(cid)
    if ch is None:
        return "Chaîne introuvable.", kb([nav("cl")])
    rows = [[(label, f"cE:{cid}:{key}")] for key, label in GROUPS]
    rows.append([(BACK, f"cv:{cid}")])
    return f"{title('Réglages', ch.name)}\n\nQue veux-tu modifier ?", kb(rows)


async def channel_group(svc: Services, cid: int, group: str) -> Screen:
    ch = await svc.get_channel(cid)
    if ch is None:
        return "Chaîne introuvable.", kb([nav("cl")])
    label = dict(GROUPS).get(group, group)
    lines = [title(label, ch.name), ""]
    rows = []
    for f in fields_in(group):
        val = f.value_text(ch)
        lines.append(row(f.label, val))
        rows.append([(f"{f.label} : {short(val, 24)}", f"cf:{cid}:{f.key}")])
    rows.append([(BACK, f"ce:{cid}"), ("Chaîne", f"cv:{cid}")])
    return "\n".join(lines), kb(rows)


async def field_choices(svc: Services, cid: int, key: str, bg_categories: list[str] | None = None) -> Screen:
    ch = await svc.get_channel(cid)
    f = FIELD_MAP[key]
    lines = [title(f.label, ch.name), ""]
    rows = []
    if f.kind == "multi":
        if key == "bg_categories":
            opts = [(c, c) for c in (bg_categories or [])]
            current = set(ch.bg_categories or [])
            lines.append("Choisis les catégories de fond. " + muted("Aucune sélection = toutes."))
        elif key == "music_tracks":
            opts = [(short(n.rsplit(".", 1)[0], 28), n) for n in svc.pipeline.music.names()]
            current = set(ch.music_tracks or [])
            lines.append("Choisis les pistes de cette chaîne. " + muted("Aucune sélection = toute la banque, au hasard."))
            if ch.music_volume <= 0:
                lines.append(muted("La bande son est coupée pour cette chaîne : règle aussi son volume."))
        else:
            opts = f.options
            current = set(getattr(ch, key) or [])
            lines.append("Choisis les formats à alterner. " + muted("Un format est tiré au hasard à chaque histoire."))
            for label, v in opts:
                fmt = FORMATS.get(v)
                if fmt:
                    mark = f"{CHECK} " if v in current else ""
                    lines += ["", f"<b>{mark}{esc(label)}</b>", muted(fmt.pitch or short(fmt.brief, 120))]
        line_: list = []
        for i, (label, v) in enumerate(opts):
            line_.append((f"{CHECK} {label}" if v in current else label, f"cm:{cid}:{key}:{i}"))
            if len(line_) == 2:
                rows.append(line_)
                line_ = []
        if line_:
            rows.append(line_)
        if not opts:
            lines.append(muted("La banque son est vide : ajoute des pistes depuis l'accueil, Bande son."
                               if key == "music_tracks" else "La banque vidéo est vide."))
    else:
        cur = f.current(ch) if f.current else getattr(ch, key)
        line: list = []
        for i, (label, v) in enumerate(f.options):
            line.append((f"{CHECK} {label}" if v == cur else label, f"co:{cid}:{key}:{i}"))
            if len(line) == 2:
                rows.append(line)
                line = []
        if line:
            rows.append(line)
        lines.append(f"Valeur actuelle : <b>{esc(f.value_text(ch))}</b>")
    rows.append([(BACK, f"cE:{cid}:{f.group}")])
    return "\n".join(lines), kb(rows)


async def voice_picker(svc: Services, cid: int, voices: list[dict], page: int) -> Screen:
    ch = await svc.get_channel(cid)
    per = 8
    pages = max(1, (len(voices) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    lines = [title("Voix", ch.name), "", row("Actuelle", ch.voice_name or ch.voice_id), muted(f"Page {page + 1} sur {pages}")]
    rows = []
    for i, v in enumerate(voices[page * per:(page + 1) * per], start=page * per):
        tags = ", ".join(x for x in (v.get("gender"), v.get("accent"), v.get("age")) if x)
        name = f"{CHECK} {v['name']}" if v["voice_id"] == ch.voice_id else v["name"]
        rows.append([(f"{short(name, 26)}{f' · {tags}' if tags else ''}"[:60], f"vsel:{cid}:{i}")])
    pager = []
    if page > 0:
        pager.append(("← Précédentes", f"vp:{cid}:{page - 1}"))
    if page < pages - 1:
        pager.append(("Suivantes →", f"vp:{cid}:{page + 1}"))
    if pager:
        rows.append(pager)
    rows.append([("Saisir un ID de voix", f"vid:{cid}")])
    rows.append([(BACK, f"cE:{cid}:voice")])
    return "\n".join(lines), kb(rows)


# ---------------------------------------------------------------------------- vidéos
async def channel_videos(svc: Services, cid: int, page: int = 0, archived: bool = False) -> Screen:
    ch = await svc.get_channel(cid)
    zone = tz(svc)
    per = 8
    flt = (Video.status == VideoStatus.ARCHIVED) if archived else (Video.status != VideoStatus.ARCHIVED)
    async with db.session() as s:
        vids = list((await s.execute(
            select(Video).where(Video.channel_id == cid, flt).order_by(Video.id.desc()).offset(page * per).limit(per + 1)
        )).scalars().all())
    more = len(vids) > per
    vids = vids[:per]
    lines = [title("Archives" if archived else "Vidéos", ch.name if ch else "?"), ""]
    if archived:
        lines += [muted("Vidéos mises de côté, sans décision. Désarchive-les pour les remettre en circuit."), ""]
    rows = []
    for v in vids:
        glyph, label = VIDEO_STATUS.get(v.status, ("·", v.status))
        part = f" · partie {v.part_index}/{v.part_count}" if v.part_count > 1 else ""
        if v.format == VideoFormat.HORIZONTAL:
            part = " · vidéo longue"
        lines.append(f"<b>#{v.id}</b>  {esc(short(v.title, 44))}")
        lines.append(muted(f"{label}{part} · {human_delta(v.created_at, zone)}"))
        lines.append("")
        rows.append([(f"{glyph} #{v.id}  {short(v.title, 32)}", f"v:{v.id}")])
    if not vids:
        lines.append(muted("Aucune vidéo archivée." if archived else "Aucune vidéo pour l'instant."))
    head = "cva" if archived else "cvv"
    pager = []
    if page > 0:
        pager.append(("← Récentes", f"{head}:{cid}:{page - 1}"))
    if more:
        pager.append(("Anciennes →", f"{head}:{cid}:{page + 1}"))
    if pager:
        rows.append(pager)
    rows.append([(BACK, f"cv:{cid}"), (HOME, "h")])
    return "\n".join(lines).rstrip(), kb(rows)


async def video_caption(svc: Services, video_id: int, *, with_text: bool = False) -> str:
    zone = tz(svc)
    async with db.session() as s:
        v = await s.get(Video, video_id)
        if v is None:
            return "Vidéo introuvable."
        ch = await s.get(Channel, v.channel_id)
        story = await s.get(Story, v.story_id)
        pubs = list((await s.execute(select(Publication).where(Publication.video_id == video_id))).scalars())
    _, label = VIDEO_STATUS.get(v.status, ("", v.status))
    fmt = FORMATS.get(story.format) if story else None
    meta = [x for x in (
        "vidéo longue" if v.format == VideoFormat.HORIZONTAL else "",
        f"partie {v.part_index}/{v.part_count}" if v.part_count > 1 else "",
        mmss(v.duration),
        fmt.label if fmt else "",
        human_delta(v.created_at, zone),
    ) if x]
    lines = [
        f"<b>{label}</b>{DOT}#{v.id}{DOT}{esc(ch.name if ch else '?')}",
        "",
        f"<b>{esc(short(v.title, 120))}</b>",
        muted(DOT.join(meta)),
    ]
    if v.bg_file:
        lines.append(muted(f"Fond : {short(v.bg_file.split('/')[-1], 40)} à {mmss(v.bg_start)}"))
    if pubs:
        lines.append("")
        for p in pubs:
            name = PLATFORM.get(p.platform, p.platform)
            state = PUB_STATUS.get(p.status, p.status)
            line = f"{name}  "
            if p.status == PubStatus.PUBLISHED and p.url:
                line += f"{CHECK} <a href=\"{esc(p.url)}\">voir la vidéo</a>"
            elif p.status == PubStatus.FAILED:
                line += f"{CROSS} {esc(short(strip_platform(p.error, name), 90)) or state}"
            elif p.status == PubStatus.DRAFT:
                line += f"{CHECK} brouillon, {muted('à finaliser dans l’app TikTok')}"
            else:
                line += esc(state)
            lines.append(line)
    if v.status == VideoStatus.SCHEDULED and v.scheduled_at:
        when = human_when(v.scheduled_at, zone)
        lines += ["", f"Programmée <b>{esc(when)}</b>"]
    if v.note:
        lines += ["", muted(v.note)]
    if v.error and v.status == VideoStatus.FAILED:
        lines += ["", f"{CROSS} {esc(short(v.error, 200))}"]
    lines.append("")
    if with_text:
        lines.append(f"<blockquote expandable>{esc(v.text[:2800])}</blockquote>")
    else:
        lines.append(muted(short(v.text, 220)))
    return "\n".join(lines)


def can_split(video: Video) -> bool:
    return (video.format != VideoFormat.HORIZONTAL and video.part_count == 1 and bool(video.file_path)
            and video.duration > 45 and video.status in (VideoStatus.PENDING_APPROVAL, VideoStatus.READY,
                                                         VideoStatus.ARCHIVED, VideoStatus.FAILED))


def video_keyboard(video: Video, *, nav: bool = True) -> InlineKeyboardMarkup:  # noqa: F811
    show_nav = nav
    vid = video.id
    st = video.status
    rows: list[list] = []
    if st == VideoStatus.PENDING_APPROVAL:
        rows.append([("Publier maintenant", f"vn:{vid}"), ("Valider", f"va:{vid}")])
        rows.append([("Programmer", f"vsc:{vid}"), ("Régénérer", f"vr:{vid}")])
        rows.append([("Archiver", f"var:{vid}"), ("Jeter", f"vx:{vid}")])
    elif st == VideoStatus.READY:
        rows.append([("Publier maintenant", f"vn:{vid}"), ("Programmer", f"vsc:{vid}")])
        rows.append([("Archiver", f"var:{vid}"), ("Jeter", f"vx:{vid}")])
    elif st == VideoStatus.SCHEDULED:
        rows.append([("Publier maintenant", f"vn:{vid}"), ("Changer la date", f"vsc:{vid}")])
        rows.append([("Annuler la programmation", f"vsu:{vid}"), ("Archiver", f"var:{vid}")])
    elif st == VideoStatus.ARCHIVED:
        rows.append([("Désarchiver", f"vua:{vid}"), ("Programmer", f"vsc:{vid}")] if video.file_path
                    else [("Désarchiver", f"vua:{vid}")])
        rows.append([("Jeter", f"vx:{vid}")])
    elif st in (VideoStatus.FAILED, VideoStatus.PUBLISHED) and video.file_path:
        rows.append([("Republier les échecs", f"vn:{vid}"), ("Jeter", f"vx:{vid}")])
        if st == VideoStatus.FAILED:
            rows.append([("Archiver", f"var:{vid}")])
    extra = [("Texte complet", f"vt:{vid}")]
    if video.file_path or video.preview_path:
        extra.insert(0, ("Aperçu", f"vf:{vid}"))
    if can_split(video):
        extra.append(("Découper", f"vsp:{vid}"))
    rows.append(extra)
    if show_nav:
        rows.append([("Chaîne", f"cv:{video.channel_id}"), (HOME, "h")])
    return kb(rows)


async def video_view(svc: Services, video_id: int, *, with_text: bool = False) -> Screen:
    v = await svc.get_video(video_id)
    if v is None:
        return "Vidéo introuvable.", kb([nav()])
    return await video_caption(svc, video_id, with_text=with_text), video_keyboard(v)


# ---------------------------------------------------------------------------- file & planning
async def queue(svc: Services) -> Screen:
    zone = tz(svc)
    jobs = svc.jobs.snapshot()
    chs = {c.id: c for c in await svc.list_channels()}
    running = [j for j in jobs if j.status == "running"]
    pending = [j for j in jobs if j.status != "running"]
    lines = [title("File d'attente"), "", "<b>En cours</b>"]
    lines += [esc(j.label) for j in running] or [muted("Rien en cours.")]
    if pending:
        lines += ["", f"<b>En attente</b>  {muted(str(len(pending)))}"]
        lines += [esc(j.label) for j in pending[:8]]

    async with db.session() as s:
        waiting = list((await s.execute(
            select(Video).where(Video.status.in_([VideoStatus.PENDING_APPROVAL, VideoStatus.READY]))
            .order_by(Video.channel_id, Video.story_id, Video.part_index).limit(20)
        )).scalars().all())
    rows = []
    if waiting:
        lines += ["", f"<b>En stock</b>  {muted(str(len(waiting)))}"]
        for v in waiting:
            glyph, label = VIDEO_STATUS.get(v.status, ("·", ""))
            name = chs[v.channel_id].name if v.channel_id in chs else "?"
            lines.append(f"#{v.id}  {esc(short(v.title, 40))}")
            lines.append(muted(f"{label} · {short(name, 28)}"))
            if len(rows) < 8:
                rows.append([(f"{glyph} #{v.id}  {short(v.title, 32)}", f"v:{v.id}")])

    async with db.session() as s:
        planned = list((await s.execute(
            select(Video).where(Video.status == VideoStatus.SCHEDULED).order_by(Video.scheduled_at).limit(10)
        )).scalars().all())
    if planned:
        lines += ["", f"<b>Programmées</b>  {muted(str(len(planned)))}"]
        for v in planned:
            name = chs[v.channel_id].name if v.channel_id in chs else "?"
            when = human_when(v.scheduled_at, zone) if v.scheduled_at else "?"
            lines.append(f"#{v.id}  {esc(short(v.title, 40))}")
            lines.append(muted(f"{when} · {short(name, 28)}"))
            if len(rows) < 10:
                rows.append([(f"◷ #{v.id}  {short(v.title, 32)}", f"v:{v.id}")])

    upcoming = svc.scheduler.upcoming(8)
    if upcoming:
        lines += ["", "<b>Prochains créneaux</b>"]
        for when, cid, kind in upcoming:
            name = chs[cid].name if cid in chs else f"#{cid}"
            tag = "  " + muted("vidéo longue") if kind == VideoFormat.HORIZONTAL else ""
            lines.append(f"{when.astimezone(zone):%d/%m %H:%M}  {esc(short(name, 28))}{tag}")
    rows.append([("Actualiser", "q"), (HOME, "h")])
    return "\n".join(lines), kb(rows)


# ---------------------------------------------------------------------------- stats
async def stats(svc: Services) -> Screen:
    o = await overview(svc, recent_limit=0, days=14)
    ser = o["series"]
    total_p, total_g = sum(ser["published"]), sum(ser["generated"])
    hi = max(ser["published"] + [1])
    chart = []
    for label, p in list(zip(ser["labels"], ser["published"]))[-10:]:
        size = int(round(p / hi * 14))
        chart.append(f"{label}  {'█' * size}{'·' * (14 - size)}  {p:>2}")
    lines = [
        title("Statistiques"),
        muted(f"14 jours · {plural(total_p, 'publication')} · {plural(total_g, 'vidéo générée', 'vidéos générées')}"),
        "",
        "<b>Publications par jour</b>",
        "<pre>" + "\n".join(chart) + "</pre>",
        "",
        "<b>Aujourd'hui par chaîne</b>",
    ]
    for c in o["channels"]:
        planned = f" sur {c['per_day']} prévues"
        lines.append(f"{esc(short(c['name'], 26))}  <b>{c['today']}</b>" + muted(planned))
    if not o["channels"]:
        lines.append(muted("Aucune chaîne."))
    t = o["today"]
    lines += ["", "<b>Consommation du jour</b>",
              row(svc.settings.llm.label, f"{kfmt(t['openai_tokens'])} tokens"),
              row("ElevenLabs", f"{kfmt(t['tts_chars'])} caractères")]
    q = o["quota"]
    if q and q.get("limit"):
        left = max(0, q["limit"] - q["used"])
        lines += [row("Forfait", q.get("tier", "") or "—") + "  " + muted(f"{num(left)} caractères restants"),
                  f"<code>{bar(left / q['limit'], 16)}</code>"]
    return "\n".join(lines), kb([[("Actualiser", "st"), (HOME, "h")]])


# ---------------------------------------------------------------------------- banque vidéo
async def bank(svc: Services) -> Screen:
    summary = await svc.pipeline.bank.summary()
    lines = [
        title("Banque vidéo"),
        muted("Une vidéo de fond est tirée au hasard, puis une séquence au hasard de la durée exacte de la narration."),
        "",
        "<b>Catégories</b>",
    ]
    if not summary:
        lines.append(muted("Vide. Dépose des vidéos dans data/backgrounds/<catégorie>/ ou ajoute-les ici."))
    total = 0.0
    for cat, info in sorted(summary.items()):
        total += info["seconds"]
        lines.append(f"{esc(cat)}  <b>{plural(info['files'], 'vidéo')}</b>  {muted(hhmm(info['seconds']))}")
    if summary:
        lines += ["", row("Total", hhmm(total))]
    lines.append(row("Ajout par lien", "disponible" if shutil.which("yt-dlp") else "yt-dlp non installé"))
    return "\n".join(lines), kb([[("Ajouter une vidéo", "bka"), ("Rescanner", "bk")], nav()])


async def music_bank(svc: Services) -> Screen:
    tracks = await svc.pipeline.music.summary()
    chs = await svc.list_channels()
    lines = [
        title("Bande son"),
        muted("Musique de fond mixée sous la voix : une piste tirée au hasard, à partir d'un moment au hasard, "
              "avec fondu d'entrée et de sortie."),
        "",
    ]
    rows: list[list] = []
    if not tracks:
        lines.append(muted("Aucune piste. Envoie un mp3, m4a, wav, ogg ou flac, ou un lien (YouTube, SoundCloud…)."))
    for i, (name, dur) in enumerate(tracks):
        lines.append(f"{i + 1}. {esc(short(name, 40))}  {muted(mmss(dur) if dur else 'illisible')}")
        if i < 30:
            rows.append([(f"{i + 1}. {short(name, 30)}", f"mut:{i}")])
    if chs:
        lines += ["", "<b>Chaînes</b>"]
        for c in chs[:12]:
            state = (f"{round(c.music_volume * 100)} %, {plural_tracks(c.music_tracks)}" if c.music_volume > 0
                     else "coupée")
            lines.append(row(short(c.name, 24), state))
        lines.append(muted("Réglage par chaîne : Chaîne, Réglages, Vidéo."))
    rows.insert(0, [("Ajouter une piste", "mua")])
    if tracks and chs and not any(c.music_volume > 0 for c in chs):
        rows.insert(1, [("Activer sur toutes les chaînes (8 %)", "mue")])
    rows.append(nav())
    return "\n".join(lines), kb(rows)


async def music_track(svc: Services, idx: int) -> Screen:
    names = svc.pipeline.music.names()
    if idx >= len(names):
        return "Piste introuvable.", kb([nav("mu")])
    name = names[idx]
    path = svc.pipeline.music.resolve(name)
    try:
        dur = await svc.pipeline.music.duration(path) if path else 0.0
    except Exception:
        dur = 0.0
    chs = await svc.list_channels()
    explicit = [c.name for c in chs if name in (c.music_tracks or [])]
    random_pool = [c.name for c in chs if c.music_volume > 0 and not c.music_tracks]
    lines = [title("Piste", short(name, 40)), "", row("Durée", mmss(dur) if dur else "illisible")]
    if path:
        lines.append(row("Taille", f"{path.stat().st_size / 1e6:.1f} Mo"))
    used = explicit + [f"{n} (au hasard)" for n in random_pool]
    lines.append(row("Utilisée par", ", ".join(used) if used else "aucune chaîne"))
    rows = [[("Écouter", f"mup:{idx}"), ("Supprimer", f"mud:{idx}")], [("← Bande son", "mu"), (HOME, "h")]]
    return "\n".join(lines), kb(rows)


async def bank_categories(svc: Services) -> Screen:
    cats = sorted(svc.pipeline.bank.categories().keys())
    rows = [[(c, f"bkc:{i}")] for i, c in enumerate(cats)]
    rows.append([("Nouvelle catégorie", "bkn")])
    rows.append([(BACK, "bk")])
    return f"{title('Ajouter une vidéo')}\n\nDans quelle catégorie ?", kb(rows)


# ---------------------------------------------------------------------------- réglages / journal
async def settings_screen(svc: Services) -> Screen:
    s = svc.settings
    paused = await svc.is_paused()
    live = bool(await db.kv_get("live_auto", False))
    lines = [
        title("Réglages système"),
        "",
        row("Système", "en pause" if paused else "actif"),
        row("Suivi en direct des générations auto", "activé" if live else "désactivé"),
        "",
        "<b>Configuration</b>",
        row("IA des histoires", f"{s.llm.label}, {s.llm.model}" + (" (gratuit)" if s.llm.free else "")),
        row("ElevenLabs", s.elevenlabs_model),
        row("Rendu", f"{s.video_width}×{s.video_height}, CRF {s.video_crf}, {s.max_concurrent_renders} à la fois"),
        row("Conservation des fichiers", plural(s.keep_videos_days, "jour")),
        row("Fuseau", s.timezone),
        row("Mini App", s.webapp_url or "non configurée"),
    ]
    version = deployed_version()
    rows = [
        [("Reprendre" if paused else "Pause globale", "sp"), ("Suivi en direct", "slv")],
        [("Diagnostic", "stt"), ("Journal", "ev")],
        [("Plateformes", "pf"), ("Clés ElevenLabs", "ek")],
    ]
    if version:
        auto = s.auto_update and not auto_update_flag(s.data_dir).exists()
        lines += [
            "",
            "<b>Version</b>",
            row("Installée", version["short"]) + ("  " + muted(short(version["message"], 60)) if version["message"] else ""),
            row("Mises à jour auto", "activées, à chaque push GitHub" if auto else "suspendues"),
        ]
        rows.append([("Suspendre les mises à jour" if auto else "Réactiver les mises à jour", "sau")])
    rows.append(nav())
    return "\n".join(lines), kb(rows)


PLATFORM_CONSOLES = {
    "youtube": ("Ouvrir Google Cloud", "https://console.cloud.google.com/apis/library/youtube.googleapis.com"),
    "tiktok": ("Ouvrir TikTok for Developers", "https://developers.tiktok.com/apps/"),
    "instagram": ("Ouvrir Meta for Developers", "https://developers.facebook.com/apps/"),
}


def _app_state(svc: Services, platform: str) -> str:
    source = svc.apps.source(platform)
    if platform == "youtube":
        client = svc.apps.google_client()
        if client is None:
            return muted("à configurer") if source is None else f"{CROSS} fichier illisible"
        kind = "Application Web" if client[0] == "web" else "Application de bureau"
        return f"{CHECK} client {kind}"
    if source is None:
        return muted("à configurer")
    return f"{CHECK} configurée" + "  " + muted("depuis le bot" if source == "bot" else "dans le .env")


async def platforms(svc: Services) -> Screen:
    base = svc.settings.public_base
    lines = [
        title("Plateformes"),
        muted("Pour publier, chaque plateforme exige une « app développeur » à ton nom (gratuite). "
              "Crée-la une seule fois en suivant le guide, puis donne ses identifiants ici : "
              "ils restent sur ton serveur."),
        "",
        f"YouTube  {_app_state(svc, 'youtube')}",
        f"TikTok  {_app_state(svc, 'tiktok')}",
        f"Instagram  {_app_state(svc, 'instagram')}",
        "",
    ]
    if svc.portal_available():
        lines.append(f"Adresse du serveur  <code>{esc(base)}</code>")
        lines.append(muted("Une fois une app configurée, connecte tes comptes : Chaîne, puis Comptes."))
    else:
        lines.append(f"{CROSS} Le serveur n'a pas d'adresse HTTPS : relance <code>bash install.sh --https</code>. "
                     "TikTok et Instagram en ont besoin.")
    rows = [[("YouTube", "pfy"), ("TikTok", "pft"), ("Instagram", "pfi")], [("← Réglages", "set"), (HOME, "h")]]
    return "\n".join(lines), kb(rows)


def _code(value: str) -> str:
    return f"<code>{esc(value)}</code>"


async def platform_guide(svc: Services, platform: str) -> Screen:
    from ..platform_apps import verifications

    base = svc.settings.public_base or "https://ton-serveur"
    published = list(await verifications()) if platform == "tiktok" else []
    state = _app_state(svc, platform)
    if platform == "youtube":
        lines = [
            title("YouTube", "Google Cloud"), "", f"État  {state}", "",
            "1. Ouvre Google Cloud, crée un projet, puis active <b>YouTube Data API v3</b>.",
            "2. <b>Écran de consentement</b> (Google Auth Platform) : type <i>Externe</i>, ajoute ton adresse Gmail "
            "comme utilisateur test.",
            "3. <b>Clients</b>, Créer un client, type <b>Application Web</b>. URI de redirection autorisée :",
            f"    {_code(base + '/oauth/youtube')}",
            "4. Télécharge le JSON du client, puis appuie sur <b>Envoyer le fichier JSON</b>.",
            "5. <b>Audience</b> : clique <i>Publier l'application</i>. Sinon Google déconnecte YouTube au bout de 7 jours. "
            "À la connexion, Google affiche « application non validée » : Paramètres avancés, puis Accéder.",
            "",
            muted("Tant que ton projet n'a pas passé l'audit de l'API YouTube, les vidéos mises en ligne restent "
                  "privées. Demande l'audit : support.google.com/youtube/contact/yt_api_form"),
        ]
        rows = [[("Envoyer le fichier JSON", "pfg")]]
    elif platform == "tiktok":
        direct = svc.apps.tiktok_direct
        lines = [
            title("TikTok", "TikTok for Developers"), "", f"État  {state}",
            "Publication  " + ("<b>directe</b> (video.publish)" if direct
                               else "<b>en brouillon</b> : tu finalises dans l'app TikTok"), "",
            "1. Ouvre TikTok for Developers, connecte-toi, puis <b>Manage apps</b>, <b>Connect an app</b>.",
            "2. Renseigne l'app (plateforme <b>Web</b>) :",
            f"    Web/Desktop URL : {_code(base)}",
            f"    Terms of Service URL : {_code(base + '/legal/terms')}",
            f"    Privacy Policy URL : {_code(base + '/legal/privacy')}",
            "3. Ajoute <b>Login Kit</b> et <b>Content Posting API</b>"
            + (" (active <i>Direct Post</i>)." if direct else " (sans <i>Direct Post</i>).") + " Redirect URI :",
            f"    {_code(base + '/oauth/tiktok')}",
            "    Scopes : <code>user.info.basic</code>, <code>video.upload</code>"
            + (", <code>video.publish</code>" if direct else "") + ". Retire tous les autres "
            "(<i>user.info.profile, user.info.stats, video.list, minis…</i>) : un scope inutile bloque la revue.",
            "4. Copie le <b>Client key</b> et le <b>Client secret</b>, puis appuie sur <b>Saisir les clés</b>.",
            "5. Envoie l'app en revue (<i>Submit for review</i>).",
            *(["6. Connecte ton compte (Chaîne, Comptes), puis <b>Réglages de publication</b> : choisis la "
               "visibilité et coche <i>Publier automatiquement</i>."] if direct else []),
            "",
            "<b>Vérification du site</b>, si TikTok la demande : choisis <b>URL prefix</b> (pas <i>Domain</i> : "
            "l'adresse sslip.io n'accepte pas d'enregistrement DNS), saisis "
            f"{_code(base + '/')}, télécharge le fichier de vérification, puis appuie sur "
            "<b>Fichier de vérification</b> et envoie-le ici. Clique ensuite <i>Verify</i> dans TikTok.",
            *([muted("Publiés : " + ", ".join(f"/{n}" for n in published))] if published else []),
            "",
            muted("Les brouillons sont le plus simple à faire valider : la vidéo arrive dans ta boîte de réception "
                  "TikTok et tu la publies en un tap. La publication directe exige en plus l'audit de TikTok ; "
                  "sans lui, les vidéos restent privées (Moi seul)."),
        ]
        rows = [[("Saisir les clés", "pfk:tiktok"), ("Fichier de vérification", "pfv")],
                [("Passer en brouillons" if direct else "Passer en publication directe", "pftd")]]
        if published:
            rows.append([("Retirer les fichiers de vérification", "pfvx")])
    else:
        lines = [
            title("Instagram", "Meta for Developers"), "", f"État  {state}", "",
            "Il faut un compte Instagram <b>professionnel</b> (Créateur ou Entreprise).",
            "",
            "1. Ouvre Meta for Developers, <b>Créer une app</b>, cas d'usage <i>Gérer les messages et le contenu "
            "sur Instagram</i>.",
            "2. <b>Configuration de l'API avec connexion Instagram</b>, étape « connexion professionnelle » : "
            "URL de redirection OAuth :",
            f"    {_code(base + '/oauth/instagram')}",
            "3. <b>Paramètres de l'app</b>, Général :",
            f"    Politique de confidentialité : {_code(base + '/legal/privacy')}",
            f"    Suppression des données : {_code(base + '/legal/data-deletion')}",
            "4. <b>Rôles de l'app</b> : ajoute ton compte Instagram comme testeur, puis accepte l'invitation dans "
            "Instagram (Paramètres, Apps et sites web).",
            "5. Copie l'<b>ID de l'app Instagram</b> et sa <b>clé secrète</b> (étape 2), puis appuie sur "
            "<b>Saisir les clés</b>.",
            "",
            muted("En mode développement, l'app publie sur les comptes testeurs : ça suffit pour tes propres comptes."),
        ]
        rows = [[("Saisir les clés", "pfk:instagram")]]
    label, url = PLATFORM_CONSOLES[platform]
    rows.append([(label, url)])
    if svc.apps.source(platform) in ("bot", "fichier"):
        rows.append([("Retirer ces identifiants", f"pfx:{platform}")])
    rows.append([("← Plateformes", "pf"), (HOME, "h")])
    return "\n".join(lines), kb(rows)


async def events(svc: Services) -> Screen:
    zone = tz(svc)
    evs = await db.recent_events(20)
    glyphs = {"info": "·", "success": CHECK, "warning": "!", "error": CROSS}
    lines = [title("Journal"), ""]
    for e in evs:
        lines.append(f"{glyphs.get(e.level, '·')} {esc(short(e.text, 140))}  {muted(human_delta(e.ts, zone))}")
    if not evs:
        lines.append(muted("Rien pour l'instant."))
    return "\n".join(lines)[:4000], kb([[("Actualiser", "ev"), (HOME, "h")]])


# ---------------------------------------------------------------------------- découpage / programmation
async def split_menu(svc: Services, video_id: int) -> Screen:
    v = await svc.get_video(video_id)
    if v is None or not can_split(v):
        return "Cette vidéo ne peut pas être découpée.", kb([nav()])
    lines = [title("Découper", f"#{v.id}"), "", f"Durée : <b>{mmss(v.duration)}</b>",
             "Chaque partie est coupée en fin de phrase, avec « Partie N » en haut, et suit le même parcours "
             "(validation ou stock). La vidéo entière est archivée.", ""]
    rows, line = [], []
    for sec in (60, 90, 120):
        n = max(1, -(-int(v.duration) // sec))
        if n < 2:
            continue
        lines.append(f"Parties de {sec} s max  {muted(f'{n} parties')}")
        line.append((f"{sec} s max", f"vsx:{video_id}:{sec}"))
    if line:
        rows.append(line)
    else:
        lines.append(muted("Trop courte pour être découpée."))
    rows.append([(BACK, f"v:{video_id}")])
    return "\n".join(lines), kb(rows)


def schedule_prompt(video: Video, channel_tz: str) -> str:
    from ..dates import EXAMPLES

    return (f"{title('Programmer', f'#{video.id}')}\n\nQuand publier cette vidéo ?\n"
            f"Envoie une date et une heure, ou choisis ci-dessous.\n\n{muted('Exemples : ' + EXAMPLES)}\n"
            f"{muted('Fuseau : ' + channel_tz)}")


def schedule_keyboard(video_id: int) -> InlineKeyboardMarkup:
    return kb([
        [("Ce soir 20:00", f"vsq:{video_id}:t20"), ("Demain 12:00", f"vsq:{video_id}:d12")],
        [("Demain 18:00", f"vsq:{video_id}:d18"), ("Demain 21:00", f"vsq:{video_id}:d21")],
        [("Annuler", f"v:{video_id}")],
    ])


def human_when(dt_utc, zone) -> str:
    local = to_local(dt_utc, zone)
    days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return f"{days[local.weekday()]} {local:%d/%m} à {local:%H:%M}"


# ---------------------------------------------------------------------------- clés ElevenLabs
async def elevenlabs_keys(svc: Services) -> Screen:
    q = await svc.elevenlabs_quota(refresh=True)
    ring = svc.pipeline.tts.keys
    zone = tz(svc)
    lines = [title("Clés ElevenLabs"), "",
             muted("Quand une clé n'a plus de crédits, la suivante prend le relais automatiquement. "
                   "Les clés ajoutées ici restent sur ton serveur."), ""]
    rows: list[list] = []
    details = (q or {}).get("keys", [])
    if not details:
        lines.append(muted("Aucune clé configurée."))
    for i, d in enumerate(details, start=1):
        source = ".env" if d["source"] == "env" else "ajoutée depuis le bot"
        if d.get("status"):
            until = datetime.fromtimestamp(d["status"].get("until", 0), timezone.utc).replace(tzinfo=None)
            state = ("invalide" if d["status"].get("reason") == "invalid_api_key"
                     else f"épuisée jusqu'au {to_local(until, zone):%d/%m}")
        elif "limit" in d:
            state = f"{num(max(0, d['limit'] - d['used']))} caractères restants sur {num(d['limit'])}"
        else:
            state = f"erreur : {d.get('error', '?')}"
        current = "  " + muted("en cours") if ring and ring.current == d["kid"] else ""
        lines.append(f"<b>{i}. {esc(d['masked'])}</b>  {muted(source)}{current}")
        lines.append(f"    {esc(state)}")
        btns = []
        if d.get("status"):
            btns.append((f"Réactiver la clé {i}", f"ekr:{d['kid']}"))
        if d["source"] == "bot":
            btns.append((f"Retirer la clé {i}", f"ekd:{d['kid']}"))
        if btns:
            rows.append(btns)
    if q and q.get("limit"):
        lines += ["", row("Total", f"{num(max(0, q['limit'] - q['used']))} caractères restants")]
    rows.insert(0, [("Ajouter une clé", "eka")])
    rows.append([("← Réglages", "set"), (HOME, "h")])
    return "\n".join(lines), kb(rows)
