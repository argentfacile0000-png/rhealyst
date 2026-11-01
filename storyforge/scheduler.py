"""Planification : créneaux de publication par chaîne + réapprovisionnement automatique."""

from __future__ import annotations

import logging
import re
import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from . import db
from .models import Channel, Video, VideoFormat, VideoStatus

if TYPE_CHECKING:
    from .core import Services

log = logging.getLogger(__name__)

TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:hH]([0-5]\d)$")


def parse_times(text: str) -> list[str]:
    """'9h, 13:30 19:00' -> ['09:00', '13:30', '19:00'] (triés, sans doublons)."""
    out = set()
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        if re.fullmatch(r"([01]?\d|2[0-3])[hH]?", tok):
            out.add(f"{int(tok.rstrip('hH')):02d}:00")
            continue
        m = TIME_RE.match(tok)
        if not m:
            raise ValueError(f"Heure invalide : {tok}")
        out.add(f"{int(m.group(1)):02d}:{m.group(2)}")
    return sorted(out)


class Scheduler:
    def __init__(self, services: Services):
        self.svc = services
        self.sched: AsyncIOScheduler | None = None

    async def start(self) -> None:
        s = self.svc.settings
        self.sched = AsyncIOScheduler(timezone=s.timezone)
        self.sched.start()
        self.sched.add_job(
            self.svc.refill, "interval", minutes=max(1, s.refill_interval_minutes), id="refill",
            replace_existing=True, max_instances=1, coalesce=True,
        )
        self.sched.add_job(
            self.svc.cleanup_files, CronTrigger(hour=4, minute=17, timezone=s.timezone), id="cleanup",
            replace_existing=True, max_instances=1,
        )
        async with db.session() as sess:
            channels = (await sess.execute(select(Channel))).scalars().all()
            scheduled = (await sess.execute(
                select(Video.id, Video.scheduled_at).where(Video.status == VideoStatus.SCHEDULED)
            )).all()
        for ch in channels:
            self.sync_channel(ch)
        # Publications programmées : recréées après un redémarrage (rattrapées si l'heure est passée)
        for vid, when in scheduled:
            if when is None:
                continue
            self.schedule_video(vid, when)

    def schedule_video(self, video_id: int, when_utc: datetime) -> None:
        """Publication ponctuelle d'une vidéo (when_utc : UTC naïf ou conscient)."""
        if not self.sched:
            return
        when = when_utc if when_utc.tzinfo else when_utc.replace(tzinfo=timezone.utc)
        if when <= datetime.now(timezone.utc):
            asyncio.get_running_loop().create_task(self.svc.publish_scheduled(video_id))
            return
        self.sched.add_job(
            self.svc.publish_scheduled, DateTrigger(run_date=when), args=[video_id], id=f"pub:{video_id}",
            replace_existing=True, misfire_grace_time=None,
        )

    def unschedule_video(self, video_id: int) -> None:
        if self.sched and self.sched.get_job(f"pub:{video_id}"):
            self.sched.remove_job(f"pub:{video_id}")

    def scheduled_runs(self) -> list[tuple[datetime, int]]:
        if not self.sched:
            return []
        return sorted((j.next_run_time, int(j.id.split(":")[1])) for j in self.sched.get_jobs()
                      if j.id.startswith("pub:") and j.next_run_time)

    async def stop(self) -> None:
        if self.sched:
            self.sched.shutdown(wait=False)

    def sync_channel(self, ch: Channel) -> None:
        """(Re)crée les créneaux de la chaîne : Shorts (vertical) et vidéos longues (horizontal)."""
        if not self.sched:
            return
        self.remove_channel(ch.id)
        if not ch.active:
            return
        plans = []
        if VideoFormat.VERTICAL in ch.formats_enabled():
            plans.append((VideoFormat.VERTICAL, ch.schedule_times or []))
        if VideoFormat.HORIZONTAL in ch.formats_enabled():
            plans.append((VideoFormat.HORIZONTAL, ch.long_schedule_times or []))
        for kind, times in plans:
            for t in times:
                m = TIME_RE.match(t)
                if not m:
                    continue
                try:
                    trigger = CronTrigger(
                        hour=int(m.group(1)), minute=int(m.group(2)), timezone=ch.timezone or self.svc.settings.timezone,
                        jitter=max(0, ch.jitter_minutes) * 60 or None,
                    )
                except Exception as exc:
                    log.error("Créneau invalide %s pour %s: %s", t, ch.name, exc)
                    continue
                self.sched.add_job(
                    self.svc.publish_slot, trigger, args=[ch.id, kind], id=f"slot:{ch.id}:{kind}:{t}",
                    replace_existing=True, misfire_grace_time=1800, coalesce=True, max_instances=1,
                )

    def remove_channel(self, channel_id: int) -> None:
        if not self.sched:
            return
        for job in list(self.sched.get_jobs()):
            if job.id.startswith(f"slot:{channel_id}:"):
                job.remove()

    def next_run(self, channel_id: int, kind: str | None = None) -> datetime | None:
        if not self.sched:
            return None
        prefix = f"slot:{channel_id}:{kind}:" if kind else f"slot:{channel_id}:"
        times = [
            j.next_run_time for j in self.sched.get_jobs()
            if j.id.startswith(prefix) and j.next_run_time is not None
        ]
        return min(times) if times else None

    def upcoming(self, limit: int = 10) -> list[tuple[datetime, int, str]]:
        """Prochains créneaux : (date, chaîne, format)."""
        if not self.sched:
            return []
        out = []
        for j in self.sched.get_jobs():
            if j.id.startswith("slot:") and j.next_run_time is not None:
                parts = j.id.split(":")
                kind = parts[2] if len(parts) > 3 else VideoFormat.VERTICAL
                out.append((j.next_run_time, int(parts[1]), kind))
        return sorted(out)[:limit]
