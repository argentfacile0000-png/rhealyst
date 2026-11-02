"""File de tâches asynchrone : rendus (CPU) et publications (réseau) en parallèle contrôlé."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: int
    kind: str  # generate | publish
    label: str
    factory: Callable[[], Awaitable[Any]] = field(repr=False)
    channel_id: int | None = None
    video_id: int | None = None
    tag: str = ""  # ex. format de vidéo (vertical / horizontal)
    status: str = "pending"  # pending | running
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None


class JobManager:
    def __init__(self, render_concurrency: int = 1, publish_concurrency: int = 2, busy_file: Path | None = None):
        # busy_file : présent tant qu'une tâche tourne (la mise à jour automatique attend qu'il disparaisse).
        self.busy_file = busy_file
        self._ids = itertools.count(1)
        self._queues: dict[str, asyncio.Queue[Job]] = {}
        self._workers: list[asyncio.Task] = []
        self._concurrency = {"generate": max(1, render_concurrency), "publish": max(1, publish_concurrency)}
        self._jobs: dict[int, Job] = {}
        self._holds = 0  # activités hors file (téléchargements par lien) qu'une mise à jour ne doit pas couper
        self.completed = 0
        self.failed = 0

    async def start(self) -> None:
        for kind, n in self._concurrency.items():
            q: asyncio.Queue[Job] = asyncio.Queue()
            self._queues[kind] = q
            for i in range(n):
                self._workers.append(asyncio.create_task(self._worker(kind, q), name=f"worker-{kind}-{i}"))
        if self.busy_file:
            self._workers.append(asyncio.create_task(self._heartbeat(), name="jobs-heartbeat"))

    async def stop(self) -> None:
        for t in self._workers:
            t.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def _mark_busy(self) -> None:
        if not self.busy_file:
            return
        try:
            if self._holds or any(j.status == "running" for j in self._jobs.values()):
                self.busy_file.parent.mkdir(parents=True, exist_ok=True)
                self.busy_file.write_text(str(int(time.time())))
            else:
                self.busy_file.unlink(missing_ok=True)
        except OSError:
            log.debug("Fichier d'activité inaccessible", exc_info=True)

    @contextlib.contextmanager
    def hold(self):
        """Signale une activité longue (ex. téléchargement) : la mise à jour automatique attend sa fin."""
        self._holds += 1
        self._mark_busy()
        try:
            yield
        finally:
            self._holds -= 1
            self._mark_busy()

    async def _heartbeat(self) -> None:
        while True:
            self._mark_busy()
            await asyncio.sleep(60)

    async def _worker(self, kind: str, q: asyncio.Queue[Job]) -> None:
        while True:
            job = await q.get()
            job.status = "running"
            job.started_at = time.time()
            self._mark_busy()
            try:
                await job.factory()
                self.completed += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failed += 1
                log.exception("Tâche %s échouée (%s)", job.id, job.label)
            finally:
                self._jobs.pop(job.id, None)
                self._mark_busy()
                q.task_done()

    def submit(
        self,
        kind: str,
        label: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        channel_id: int | None = None,
        video_id: int | None = None,
        tag: str = "",
    ) -> Job:
        if kind not in self._queues:
            raise RuntimeError("JobManager non démarré")
        job = Job(next(self._ids), kind, label, factory, channel_id=channel_id, video_id=video_id, tag=tag)
        self._jobs[job.id] = job
        self._queues[kind].put_nowait(job)
        return job

    def snapshot(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: (j.status != "running", j.created_at))

    def count(self, kind: str | None = None, channel_id: int | None = None, video_id: int | None = None,
              tag: str | None = None) -> int:
        return sum(
            1
            for j in self._jobs.values()
            if (kind is None or j.kind == kind)
            and (channel_id is None or j.channel_id == channel_id)
            and (video_id is None or j.video_id == video_id)
            and (tag is None or j.tag == tag)
        )
