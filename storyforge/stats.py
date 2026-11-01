"""Agrégats pour les dashboards (Telegram + Mini App)."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from . import db
from .models import Channel, Publication, PubStatus, Story, Video, VideoStatus
from .story.styles import get_language

if TYPE_CHECKING:
    from .core import Services


def tz(svc: Services) -> ZoneInfo:
    try:
        return ZoneInfo(svc.settings.timezone)
    except Exception:
        return ZoneInfo("UTC")


def to_local(dt: datetime | None, zone: ZoneInfo) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone)


def local_midnight_utc(zone: ZoneInfo, days_ago: int = 0) -> datetime:
    now = datetime.now(zone)
    midnight = (now - timedelta(days=days_ago)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


def human_delta(dt: datetime | None, zone: ZoneInfo) -> str:
    if dt is None:
        return "—"
    local = to_local(dt, zone)
    now = datetime.now(zone)
    diff = local - now
    secs = int(diff.total_seconds())
    if secs >= 0:
        if secs < 3600:
            return f"dans {max(1, secs // 60)} min"
        if local.date() == now.date():
            return f"à {local:%H:%M}"
        if local.date() == (now + timedelta(days=1)).date():
            return f"demain {local:%H:%M}"
        return f"{local:%d/%m %H:%M}"
    secs = -secs
    if secs < 60:
        return "à l'instant"
    if secs < 3600:
        return f"il y a {secs // 60} min"
    if secs < 86400:
        return f"il y a {secs // 3600} h"
    return f"il y a {secs // 86400} j"


def uptime(svc: Services) -> str:
    secs = int(time.time() - svc.started_at)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}j {h}h" if d else f"{h}h{m:02d}" if h else f"{m} min"


async def overview(svc: Services, *, recent_limit: int = 8, days: int = 14) -> dict[str, Any]:
    zone = tz(svc)
    today0 = local_midnight_utc(zone)
    start = local_midnight_utc(zone, days - 1)

    async with db.session() as s:
        channels = list((await s.execute(select(Channel).order_by(Channel.id))).scalars().all())
        status_counts: dict[str, int] = defaultdict(int)
        for ch_id, st, fmt, n in (await s.execute(
            select(Video.channel_id, Video.status, Video.format, func.count(Video.id))
            .where(Video.status.in_([VideoStatus.READY, VideoStatus.PENDING_APPROVAL, VideoStatus.RENDERING]))
            .group_by(Video.channel_id, Video.status, Video.format)
        )).all():
            status_counts[f"{ch_id}:{st}"] += n
            status_counts[f"{ch_id}:{st}:{fmt}"] += n
        gen_today = (await s.execute(
            select(func.count(Video.id)).where(Video.created_at >= today0, Video.status != VideoStatus.FAILED)
        )).scalar_one()
        failed_today = (await s.execute(
            select(func.count(Video.id)).where(Video.created_at >= today0, Video.status == VideoStatus.FAILED)
        )).scalar_one()
        pub_rows = (await s.execute(
            select(Publication.platform, Publication.status, Publication.channel_id, Publication.published_at, Publication.created_at)
            .where(Publication.created_at >= start)
        )).all()
        vid_rows = (await s.execute(
            select(Video.created_at, Video.status).where(Video.created_at >= start)
        )).all()
        recent = list((await s.execute(
            select(Video).order_by(Video.id.desc()).limit(recent_limit)
        )).scalars().all())
        recent_pubs: dict[int, list[Publication]] = defaultdict(list)
        if recent:
            for p in (await s.execute(
                select(Publication).where(Publication.video_id.in_([v.id for v in recent]))
            )).scalars():
                recent_pubs[p.video_id].append(p)
        tokens = (await s.execute(
            select(func.coalesce(func.sum(Story.prompt_tokens), 0), func.coalesce(func.sum(Story.completion_tokens), 0))
            .where(Story.created_at >= today0)
        )).one()
        chars_today = (await s.execute(
            select(func.coalesce(func.sum(Video.tts_chars), 0)).where(Video.created_at >= today0)
        )).scalar_one()

    # Séries quotidiennes
    labels = [(datetime.now(zone) - timedelta(days=i)).date() for i in range(days - 1, -1, -1)]
    pub_series = {d: 0 for d in labels}
    gen_series = {d: 0 for d in labels}
    pub_today = {"youtube": 0, "tiktok": 0, "instagram": 0}
    pub_failed_today = 0
    per_channel_today: dict[int, int] = defaultdict(int)
    for platform, status, ch_id, pub_at, created in pub_rows:
        if status in PubStatus.OK and pub_at:
            d = to_local(pub_at, zone).date()
            if d in pub_series:
                pub_series[d] += 1
            if pub_at >= today0:
                pub_today[platform] = pub_today.get(platform, 0) + 1
                per_channel_today[ch_id] += 1
        elif status == PubStatus.FAILED and created >= today0:
            pub_failed_today += 1
    for created, status in vid_rows:
        if status == VideoStatus.FAILED:
            continue
        d = to_local(created, zone).date()
        if d in gen_series:
            gen_series[d] += 1

    ch_by_id = {c.id: c for c in channels}
    ch_list = []
    for c in channels:
        nxt = svc.scheduler.next_run(c.id)
        ch_list.append({
            "id": c.id,
            "name": c.name,
            "active": c.active,
            "language": c.language,
            "flag": get_language(c.language).flag,
            "ready": status_counts.get(f"{c.id}:{VideoStatus.READY}", 0),
            "pending": status_counts.get(f"{c.id}:{VideoStatus.PENDING_APPROVAL}", 0),
            "rendering": status_counts.get(f"{c.id}:{VideoStatus.RENDERING}", 0),
            "per_day": len(c.schedule_times or []),
            "schedule": c.schedule_times or [],
            "next_slot": human_delta(nxt.astimezone(timezone.utc).replace(tzinfo=None), zone) if nxt else "—",
            "approval": c.approval_required,
            "youtube": {"enabled": c.youtube_enabled, "linked": c.youtube_linked, "account": c.youtube_account},
            "tiktok": {"enabled": c.tiktok_enabled, "linked": c.tiktok_linked, "account": c.tiktok_account},
            "instagram": {"enabled": c.instagram_enabled, "linked": c.instagram_linked, "account": c.instagram_account},
            "shorts_enabled": c.shorts_enabled,
            "long_enabled": c.long_enabled,
            "long_ready": status_counts.get(f"{c.id}:{VideoStatus.READY}:horizontal", 0),
            "long_per_day": len(c.long_schedule_times or []) if c.long_enabled else 0,
            "today": per_channel_today.get(c.id, 0),
        })

    recent_list = []
    for v in recent:
        ch = ch_by_id.get(v.channel_id)
        recent_list.append({
            "id": v.id,
            "channel": ch.name if ch else "?",
            "channel_id": v.channel_id,
            "title": v.title,
            "part": f"{v.part_index}/{v.part_count}" if v.part_count > 1 else "",
            "format": v.format,
            "status": v.status,
            "duration": round(v.duration, 1),
            "created": human_delta(v.created_at, zone),
            "has_thumb": bool(v.thumb_path),
            "has_file": bool(v.file_path),
            "error": v.error,
            "pubs": [
                {"platform": p.platform, "status": p.status, "url": p.url, "error": p.error}
                for p in recent_pubs.get(v.id, [])
            ],
        })

    jobs = [
        {"id": j.id, "kind": j.kind, "label": j.label, "status": j.status,
         "since": int(time.time() - (j.started_at or j.created_at))}
        for j in svc.jobs.snapshot()
    ]

    return {
        "paused": await svc.is_paused(),
        "uptime": uptime(svc),
        "today": {
            "generated": gen_today,
            "published": sum(pub_today.values()),
            "youtube": pub_today.get("youtube", 0),
            "tiktok": pub_today.get("tiktok", 0),
            "instagram": pub_today.get("instagram", 0),
            "failed": failed_today + pub_failed_today,
            "openai_tokens": int(tokens[0]) + int(tokens[1]),
            "tts_chars": int(chars_today),
        },
        "pending_approval": sum(c["pending"] for c in ch_list),
        "ready": sum(c["ready"] for c in ch_list),
        "channels": ch_list,
        "series": {
            "labels": [d.strftime("%d/%m") for d in labels],
            "published": [pub_series[d] for d in labels],
            "generated": [gen_series[d] for d in labels],
        },
        "recent": recent_list,
        "jobs": jobs,
        "quota": await svc.elevenlabs_quota(),
    }
