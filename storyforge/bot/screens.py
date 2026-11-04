"""Écrans du dashboard Telegram (texte HTML + clavier), style Minimal Design System."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from telegram import InlineKeyboardMarkup

from .. import db
from ..models import Channel, Publication, PubStatus, Story, Video, VideoFormat, VideoStatus
from ..stats import human_delta, overview, to_local, tz
from ..story.styles import FORMATS, get_language
from ..tools import ytdlp_command
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
    esc,
    hhmm,
    kb,
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
    chs = o["channels"]
    active = [c for c in chs if c["active"]]
    lines = [
        f"<b>StoryForge</b>  {muted(f'{OFF} En pause' if o['paused'] else f'{ON} Actif')}",
        "",
        row("À valider", o["pending_approval"]) + "   " + row("Prêtes", o["ready"]),
        row("Aujourd'hui", f"{t['generated']} générées, {t['published']} publiées"
            + (f", {t['failed']} échecs" if t["failed"] else "")),
    ]
    nxt = [c for c in active if c["next_slot"] != "—"]
    if nxt:
        lines.append(row("Prochaine publication", f"{nxt[0]['next_slot']}, {short(nxt[0]['name'], 24)}"))
    lines.append(row("Chaînes actives", f"{len(active)} sur {len(chs)}"))
    running = [j for j in o["jobs"] if j["status"] == "running"]
    if running:
        lines.append(row("En cours", f"{short(running[0]['label'], 36)}  {mmss(running[0]['since'])}"))
    q = o["quota"]
    if q and q.get("limit"):
        left = max(0, q["limit"] - q["used"])
        lines.append(row("Crédits ElevenLabs", f"{num(left)} sur {num(q['limit'])}"))
    missing = svc.missing_prereqs()
    if missing:
        lines += ["", f"{CROSS} <b>À configurer</b> : {esc(', '.join(missing))}"]
    elif not chs:
        lines += ["", muted("Commence par créer une chaîne.")]

    pending = o["pending_approval"]
    rows = [
        [(f"À valider ({pending})" if pending else "Vidéos", "q"), ("Chaînes", "cl")],
        [("Générer", "gen"), ("Médias", "md")],
        [("Réglages", "set"), ("Reprendre" if o["paused"] else "Pause", "sp")],
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
             row("Vidéo longue", "histoire complète en 16:9, sans découpage") + "  " + muted("YouTube")]
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
        f"<b>{esc(ch.name)}</b>  " + muted(f"{ON} Active" if ch.active else f"{OFF} En pause") + f"  {muted(lang.label)}",
        "",
        "<b>Contenu</b>",
        row("Histoires", _formats_label(ch)),
        row("Shorts", f"{ch.min_seconds}–{ch.max_seconds} s" + (f", parties de {ch.split_seconds} s" if ch.split_seconds
                                                              else "") if ch.shorts_enabled else "désactivés"),
        row("Vidéos longues", "histoire complète en 16:9, YouTube" if ch.long_enabled else "désactivées"),
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
        [("Contenu", f"cE:{cid}:content"), ("Apparence", f"cE:{cid}:look")],
        [("Planning", f"cE:{cid}:plan"), ("Comptes", f"ca:{cid}")],
        [("Vidéos", f"cvv:{cid}:0"), ("Archives", f"cva:{cid}:0"), ("Publier la suivante", f"cp:{cid}")],
        [("← Chaînes", "cl"), ("Supprimer", f"cd:{cid}"), (HOME, "h")],
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
        lines.append(f"{CROSS} La page de connexion a besoin d'une adresse HTTPS : relance "
                     "<code>bash install.sh --https</code> sur le serveur.")
    rows.append([("Réglages de publication", f"cE:{cid}:pub")])
    if any(svc.platform_problem(p, cid) for p in ("youtube", "tiktok", "instagram")):
        rows.append([("Configurer les plateformes", "pf")])
    unlink = [(f"Déconnecter {PLATFORM[p]}", f"cu:{cid}:{p}")
              for p in ("youtube", "tiktok", "instagram") if getattr(ch, f"{p}_linked")]
    for i in range(0, len(unlink), 2):
        rows.append(unlink[i:i + 2])
    rows.append([("← Chaîne", f"cv:{cid}"), (HOME, "h")])
    return "\n".join(lines), kb(rows)


async def channel_edit(svc: Services, cid: int) -> Screen:
    return await channel_view(svc, cid)


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
    if group == "pub":
        rows.append([("← Comptes", f"ca:{cid}"), (HOME, "h")])
    else:
        rows.append([("← Chaîne", f"cv:{cid}"), (HOME, "h")])
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
    rows.append([(BACK, f"cE:{cid}:content")])
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


def video_keyboard(video: Video, *, nav: bool = True, more: bool = False) -> InlineKeyboardMarkup:  # noqa: F811
    vid = video.id
    st = video.status
    rows: list[list] = []
    if st in (VideoStatus.PENDING_APPROVAL, VideoStatus.READY):
        rows.append([("Publier maintenant", f"vn:{vid}"), ("Programmer", f"vsc:{vid}")])
        if st == VideoStatus.PENDING_APPROVAL:
            rows.append([("Valider pour le planning", f"va:{vid}"), ("Jeter", f"vx:{vid}")])
        else:
            rows.append([("Jeter", f"vx:{vid}")])
    elif st == VideoStatus.SCHEDULED:
        rows.append([("Publier maintenant", f"vn:{vid}"), ("Changer la date", f"vsc:{vid}")])
        rows.append([("Annuler la programmation", f"vsu:{vid}")])
    elif st == VideoStatus.ARCHIVED:
        rows.append([("Désarchiver", f"vua:{vid}"), ("Jeter", f"vx:{vid}")])
    elif st in (VideoStatus.FAILED, VideoStatus.PUBLISHED) and video.file_path:
        rows.append([("Republier les échecs", f"vn:{vid}"), ("Jeter", f"vx:{vid}")])
    if more:
        extra = []
        if video.file_path or video.preview_path:
            extra.append(("Aperçu", f"vf:{vid}"))
        extra.append(("Texte complet", f"vt:{vid}"))
        rows.append(extra)
        extra = []
        if st == VideoStatus.PENDING_APPROVAL:
            extra.append(("Régénérer", f"vr:{vid}"))
        if st not in (VideoStatus.ARCHIVED, VideoStatus.PUBLISHED, VideoStatus.PUBLISHING, VideoStatus.RENDERING):
            extra.append(("Archiver", f"var:{vid}"))
        if can_split(video):
            extra.append(("Découper", f"vsp:{vid}"))
        if extra:
            rows.append(extra)
        rows.append([("Moins", f"vml:{vid}")])
    else:
        rows.append([("Plus d'actions", f"vm:{vid}")])
    if nav:
        rows.append([("Chaîne", f"cv:{video.channel_id}"), ("Vidéos", "q"), (HOME, "h")])
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
    lines = [title("Vidéos")]
    if running:
        lines += ["", "<b>En cours</b>"] + [esc(j.label) for j in running]
    if pending:
        lines += ["", f"<b>En attente</b>  {muted(str(len(pending)))}"]
        lines += [esc(j.label) for j in pending[:8]]

    async with db.session() as s:
        waiting = list((await s.execute(
            select(Video).where(Video.status.in_([VideoStatus.PENDING_APPROVAL, VideoStatus.READY]))
            .order_by(Video.status != VideoStatus.PENDING_APPROVAL, Video.channel_id, Video.story_id,
                      Video.part_index).limit(20)
        )).scalars().all())
    rows = []
    if waiting:
        lines += ["", f"<b>À valider et prêtes</b>  {muted(str(len(waiting)))}"]
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
    if not (running or pending or waiting or planned):
        lines += ["", muted("Aucune vidéo en attente. Lance une génération depuis l'accueil.")]
    rows.append([("Actualiser", "q"), (HOME, "h")])
    return "\n".join(lines), kb(rows)


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
    lines.append(row("Ajout par lien", "disponible" if ytdlp_command() else "yt-dlp non installé"))
    lines += ["", muted("Astuce : envoie un lien à tout moment, le bot te propose où l'ajouter.")]
    cats = sorted(summary)
    cat_rows = [[(f"{c} ({summary[c]['files']})", f"bkv:{i}:0")] for i, c in enumerate(cats[:20])]
    return "\n".join(lines), kb(cat_rows + [[("Ajouter (fichier ou lien)", "bka"), ("Rescanner", "bk")],
                                 [("Réglages YouTube (cookies, proxies)", "yt")], [("← Médias", "md"), (HOME, "h")]])


async def ytdlp_settings(svc: Services) -> Screen:
    from ..tools import proxora_proxies, proxora_status

    await proxora_proxies()  # liste en cache (10 min) : lecture rapide
    st = proxora_status()
    age = f", mise à jour il y a {int(st['age'] // 60)} min" if st["age"] is not None else ""
    lines = [
        title("Réglages YouTube"),
        muted("Pour ajouter une vidéo : envoie simplement son lien au bot, ou Banque vidéo, Ajouter."),
        "",
        row("yt-dlp", "installé" if ytdlp_command() else "non installé"),
        row("Proxies gratuits Proxora", f"{st['count']} disponibles{age}" if st["count"]
            else f"indisponibles ({st['error'] or 'liste vide'})"),
        row("Mis de côté (échec récent)", st["bad"]),
        "",
        muted("Chaque lien est d'abord téléchargé en direct. Si YouTube bloque le serveur (adresse de datacenter), "
              "je passe automatiquement par des proxies gratuits de Proxora, vérifiés récemment, en privilégiant "
              "les sorties résidentielles et mobiles. Aucun réglage à faire."),
    ]
    rows = [[("Tester avec YouTube", "ytt"), ("Rafraîchir les proxies", "ytr")],
            [("Ajouter une vidéo", "bka")],
            [("← Médias", "md"), ("← Réglages", "set")]]
    return "\n".join(lines), kb(rows)


async def bank_category(svc: Services, cat_idx: int, page: int = 0) -> Screen:
    """Vidéos d'une catégorie de fonds : durée, taille, suppression."""
    cats = sorted(svc.pipeline.bank.categories().keys())
    if not 0 <= cat_idx < len(cats):
        return "Catégorie introuvable.", kb([[("← Vidéos de fond", "bk")]])
    cat = cats[cat_idx]
    files = sorted(svc.pipeline.bank.categories()[cat], key=lambda p: p.name.lower())
    per = 8
    pages = max(1, (len(files) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    lines = [title("Vidéos de fond", cat), muted(f"{plural(len(files), 'vidéo')}" + (f", page {page + 1} sur {pages}"
                                                                                     if pages > 1 else "")), ""]
    rows: list[list] = []
    for i, f in enumerate(files[page * per:(page + 1) * per], start=page * per):
        try:
            meta = await svc.pipeline.bank.info(f)
        except Exception:
            meta = {"duration": 0.0}
        dur = float(meta.get("duration", 0.0))
        fmt = {"vertical": "9:16", "horizontal": "16:9", "square": "carré"}.get(meta.get("orientation", ""), "?")
        size = f.stat().st_size / 1e6 if f.exists() else 0
        lines.append(f"{i + 1}. {esc(short(f.name, 44))}  {muted(f'{mmss(dur)} · {fmt} · {size:.0f} Mo')}")
        rows.append([(f"Supprimer {i + 1}. {short(f.name, 28)}", f"bkd:{cat_idx}:{i}")])
    if not files:
        lines.append(muted("Catégorie vide."))
        rows.append([("Supprimer la catégorie", f"bkx:{cat_idx}")])
    pager = []
    if page > 0:
        pager.append(("← Précédentes", f"bkv:{cat_idx}:{page - 1}"))
    if page < pages - 1:
        pager.append(("Suivantes →", f"bkv:{cat_idx}:{page + 1}"))
    if pager:
        rows.append(pager)
    rows.append([("Ajouter ici", f"bkc:{cat_idx}"), ("← Vidéos de fond", "bk")])
    return "\n".join(lines), kb(rows)


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
        lines.append(muted("Réglage par chaîne : Chaîne, Apparence."))
    rows.insert(0, [("Ajouter une piste", "mua")])
    if tracks and chs and not any(c.music_volume > 0 for c in chs):
        rows.insert(1, [("Activer sur toutes les chaînes (8 %)", "mue")])
    rows.append([("← Médias", "md"), (HOME, "h")])
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
    ai = svc.pipeline.llm_keys
    el = svc.pipeline.tts.keys
    ai_ok = len(ai.available()) if ai else 0
    el_ok = len(el.available()) if el else 0
    lines = [
        title("Réglages"),
        "",
        row("Clés d'IA", f"{ai_ok} active{'s' if ai_ok > 1 else ''} sur {len(ai.all()) if ai else 0}"),
        row("Clés ElevenLabs", f"{el_ok} active{'s' if el_ok > 1 else ''} sur {len(el.all()) if el else 0}"),
        row("Plateformes", ", ".join(p for p in ("YouTube", "TikTok", "Instagram")
                                    if svc.apps.source(p.lower())) or "aucune configurée"),
        row("Mini App", "en ligne" if s.webapp_url else "non configurée"),
    ]
    version = deployed_version()
    auto = s.auto_update and not auto_update_flag(s.data_dir).exists()
    if version:
        lines.append(row("Version", version["short"]) + "  " + muted("mises à jour auto" if auto else "mises à jour suspendues"))
    rows = [
        [("Clés API", "ak"), ("Plateformes", "pf")],
        [("Prompt des histoires", "sp0"), ("Réglages YouTube", "yt")],
        [("Journal", "ev"), ("État du système", "stt")],
        [("Mode débogage", "dbg")],
    ]
    if version:
        rows.append([("Suspendre les mises à jour auto" if auto else "Réactiver les mises à jour auto", "sau")])
    rows.append(nav())
    return "\n".join(lines), kb(rows)


async def api_keys(svc: Services) -> Screen:
    """Clés d'IA et ElevenLabs : ajoutées depuis le bot, essayées dans l'ordre, bascule automatique."""
    zone = tz(svc)
    ai = svc.pipeline.llm_keys
    lines = [title("Clés API"),
             muted("Ajoute plusieurs clés : si l'une échoue (quota, limite, clé invalide), la suivante prend "
                   "le relais et je te préviens. Tes messages contenant une clé sont supprimés."), "",
             "<b>IA qui écrit les histoires</b>"]
    rows: list[list] = [[("Ajouter une clé", "aka")]]
    keys = ai.all() if ai else []
    if not keys:
        lines.append(muted("Aucune clé. Gratuit : Gemini (aistudio.google.com/apikey) ou Groq (console.groq.com/keys)."))
    for i, k in enumerate(keys, start=1):
        st = ai.status(k)
        if st:
            until = datetime.fromtimestamp(st["until"], timezone.utc).replace(tzinfo=None)
            state = (f"{CROSS} invalide" if st["reason"] == "invalide"
                     else f"{st['reason']}, reprise {human_when(until, zone)}")
        else:
            state = f"{CHECK} en cours" if ai.current == k.kid else "prête"
        src = "  " + muted(".env") if k.source == "env" else ""
        cfg = k.config(svc.settings)
        where = f", {k.region or 'us-east-1'}" if k.provider == "bedrock" else ""
        lines.append(f"{i}. <b>{esc(k.label)}</b>  <code>{esc(k.masked)}</code>  {state}{src}")
        lines.append(f"    {muted(f'modèle {cfg.model}{where}')}")
        btns = [(f"Modèle {i}", f"akm:{k.kid}")] if k.source == "bot" else []
        if st:
            btns.append((f"Réactiver {i}", f"akr:{k.kid}"))
        if k.source == "bot":
            if i > 1:
                btns.append((f"↑ {i}", f"aku:{k.kid}"))
            btns.append((f"Retirer {i}", f"akd:{k.kid}"))
        if btns:
            rows.append(btns)
    lines += ["", "<b>ElevenLabs (voix)</b>"]
    el = svc.pipeline.tts.keys
    ekeys = el.all() if el else []
    if not ekeys:
        lines.append(muted("Aucune clé. Crée-la sur elevenlabs.io, Profil, API Keys."))
    for i, k in enumerate(ekeys, start=1):
        st = el.status(k)
        state = (f"{CROSS} {st.get('reason', 'indisponible')}" if st
                 else (f"{CHECK} en cours" if el.current == k.kid else "prête"))
        src = "  " + muted(".env") if k.source == "env" else ""
        lines.append(f"{i}. <code>{esc(k.masked)}</code>  {state}{src}")
    if ekeys:
        rows.append([("Crédits et gestion ElevenLabs", "ek")])
    if any(k.source == "env" for k in keys + ekeys) or svc.pipeline.llm_keys.env_keys() or (el and el.env_keys()):
        lines += ["", muted("Les clés du .env sont reprises ici automatiquement ; pour en retirer une définitivement, "
                            "efface-la aussi du .env du serveur.")]
    rows.append([("← Réglages", "set"), (HOME, "h")])
    return "\n".join(lines), kb(rows)


async def debug_start(svc: Services) -> Screen:
    chs = await svc.list_channels()
    lines = [title("Mode débogage"),
             muted("Génère une vidéo étape par étape : tu lances chaque étape avec un bouton et tu vois tout ce qui "
                   "se passe (prompts, réponse brute de l'IA, voix, fond, sous-titres, montage), avec les fichiers. "
                   "Rien n'est publié ; à la fin tu peux garder la vidéo."), "", "Quelle chaîne, quel format ?"]
    rows = [[(f"{short(c.name, 22)} · Short", f"dbgc:{c.id}:v"), ("16:9", f"dbgc:{c.id}:h")] for c in chs[:12]]
    if not chs:
        lines.append(muted("Crée d'abord une chaîne."))
    rows.append([("← Réglages", "set"), (HOME, "h")])
    return "\n".join(lines), kb(rows)


def debug_view(svc: Services, sess, running: str | None = None) -> Screen:
    from ..debug import LABELS, STEPS

    glyph = {"ok": CHECK, "error": CROSS, "todo": "○"}
    lines = [title("Débogage", "16:9" if sess.horizontal else "Short"), ""]
    last = None
    for key, label in STEPS:
        st = sess.steps[key]
        mark = "…" if key == running else glyph[st.status]
        timing = f"  {muted(f'{st.seconds} s')}" if st.status != "todo" else ""
        lines.append(f"{mark}  {label}{timing}")
        if st.status != "todo":
            last = key
    if running:
        lines += ["", muted(f"{LABELS[running]} en cours…")]
    elif last:
        st = sess.steps[last]
        lines += ["", f"<b>{LABELS[last]}</b>"]
        body = "\n".join(st.lines)
        lines.append(f"<blockquote expandable>{esc(body[-2800:])}</blockquote>" if body else muted("Aucun journal."))
    rows: list[list] = []
    if not running:
        nxt = sess.next_step()
        if nxt and sess.can_run(nxt):
            rows.append([(f"▶ {LABELS[nxt]}", f"dbgr:{nxt}")])
        if last:
            rows.append([(f"↻ Refaire : {LABELS[last]}", f"dbgr:{last}"), ("Fichiers", f"dbgf:{last}")])
        if last and last != "story":
            rows.append([("↻ Tout reprendre à l'histoire", "dbgr:story")])
        if sess.video_id:
            rows.append([("Voir la vidéo enregistrée", f"v:{sess.video_id}")])
    rows.append([("Arrêter le débogage", "dbgx")])
    return "\n".join(lines), kb(rows)


def story_prompt(svc: Services) -> Screen:
    from ..story.prompts import SYSTEM_PROMPT

    custom = svc.pipeline.stories.system_prompt
    text = custom or SYSTEM_PROMPT
    lines = [
        title("Prompt des histoires"),
        muted("Le prompt système envoyé à l'IA avant chaque histoire : style, ton, règles d'écriture, format de "
              "réponse. La demande propre à chaque vidéo (format, durée, langue, consignes de la chaîne) s'y ajoute."),
        "",
        row("Version", "personnalisée" if custom else "originale") + "  " + muted(f"{len(text)} caractères"),
        "",
        f"<blockquote expandable>{esc(text[:3000])}{'…' if len(text) > 3000 else ''}</blockquote>",
        "",
        muted("Pour le modifier : « Recevoir le texte », copie-le, retouche-le puis renvoie-le avec « Modifier ». "
              "Garde la consigne de réponse en JSON, sinon les histoires ne pourront pas être lues."),
    ]
    rows = [[("Recevoir le texte", "spf"), ("Modifier", "spe")]]
    if custom:
        rows.append([("Rétablir l'original", "spr")])
    rows.append([("← Réglages", "set"), (HOME, "h")])
    return "\n".join(lines), kb(rows)


def api_key_models(svc: Services, kid: str, models: list[str], page: int = 0) -> Screen:
    """Choix du modèle d'une clé d'IA : liste du fournisseur (ou suggestions) + saisie libre."""
    k = svc.pipeline.llm_keys.get(kid)
    if k is None:
        return "Clé introuvable.", kb([[("← Clés API", "ak")]])
    current = k.config(svc.settings).model
    per = 10
    pages = max(1, (len(models) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    lines = [title("Modèle", f"{k.label} {k.masked}"), "", row("Actuel", current)]
    if k.provider == "bedrock":
        lines.append(row("Région", k.region or "us-east-1"))
        lines.append(muted("Le modèle doit être activé dans ta console AWS (Bedrock, Accès aux modèles). Si Bedrock "
                           "demande un profil d'inférence, prends l'ID préfixé us., eu., apac. ou global."))
    lines.append(muted(f"Page {page + 1} sur {pages}") if pages > 1 else "")
    rows = [[(f"{CHECK} {short(m, 40)}" if m == current else short(m, 44), f"akms:{kid}:{i}")]
            for i, m in enumerate(models[page * per:(page + 1) * per], start=page * per)]
    pager = []
    if page > 0:
        pager.append(("← Précédents", f"akmp:{kid}:{page - 1}"))
    if page < pages - 1:
        pager.append(("Suivants →", f"akmp:{kid}:{page + 1}"))
    if pager:
        rows.append(pager)
    extra = [("Autre modèle (saisir l'ID)", f"akmo:{kid}")]
    if k.provider == "bedrock":
        extra.append(("Changer de région", f"akg:{kid}"))
    rows.append(extra)
    rows.append([("← Clés API", "ak"), (HOME, "h")])
    return "\n".join(x for x in lines if x is not None), kb(rows)


def region_picker(kid: str = "") -> Screen:
    from ..story.llm_keys import BEDROCK_REGIONS

    rows = [[(f"{code} · {name}", f"akgs:{kid}:{i}")] for i, (code, name) in enumerate(BEDROCK_REGIONS)]
    rows.append([("Autre région (saisir)", f"akgo:{kid}")])
    rows.append([("Annuler", "ak")])
    return (f"{title('Région AWS')}\n\nDans quelle région utilises-tu Bedrock ?\n"
            + muted("C'est la région où tu as activé les modèles dans la console Bedrock.")), kb(rows)


async def media(svc: Services) -> Screen:
    """Banque vidéo et bande son sur un seul écran."""
    summary = await svc.pipeline.bank.summary()
    tracks = await svc.pipeline.music.summary()
    lines = [title("Médias"), "", "<b>Vidéos de fond</b>"]
    if summary:
        total = sum(i["seconds"] for i in summary.values())
        for cat, info in sorted(summary.items()):
            lines.append(f"{esc(cat)}  <b>{plural(info['files'], 'vidéo')}</b>  {muted(hhmm(info['seconds']))}  "
                         + muted(f"{info['vertical']} verticale(s), {info['horizontal']} horizontale(s)"))
        lines.append(muted(f"Total {hhmm(total)}"))
        chs = await svc.list_channels()
        if any(c.long_enabled for c in chs) and not any(i["horizontal"] for i in summary.values()):
            lines.append(muted("Aucun fond horizontal : les vidéos longues 16:9 recadreront des fonds verticaux. "
                               "Ajoute des vidéos horizontales pour un rendu net."))
        if any(c.shorts_enabled for c in chs) and not any(i["vertical"] for i in summary.values()):
            lines.append(muted("Aucun fond vertical : les Shorts recadreront des fonds horizontaux."))
    else:
        lines.append(muted("Aucune vidéo de fond : ajoutes-en une pour pouvoir générer."))
    lines += ["", "<b>Bande son</b>"]
    if tracks:
        lines.append(f"{plural(len(tracks), 'piste')}  {muted(hhmm(sum(d for _, d in tracks)))}")
    else:
        lines.append(muted("Aucune piste."))
    lines += ["", muted("Astuce : envoie un lien ou un fichier au bot à tout moment, il te propose où l'ajouter.")]
    rows = [
        [("Ajouter une vidéo", "bka"), ("Ajouter une piste", "mua")],
        [("Détail des vidéos", "bk"), ("Pistes", "mu")],
        [("Réglages YouTube", "yt")],
        nav(),
    ]
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
    return "\n".join(lines)[:4000], kb([[("Actualiser", "ev"), ("← Réglages", "set"), (HOME, "h")]])


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
    rows.append([("← Clés API", "ak"), (HOME, "h")])
    return "\n".join(lines), kb(rows)
