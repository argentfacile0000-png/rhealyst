"""Petites aides asynchrones autour de ffmpeg / ffprobe."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger(__name__)

ProgressCb = Callable[[float], Awaitable[None] | None]


class FFmpegError(RuntimeError):
    pass


async def run_ffmpeg(
    args: list[str],
    *,
    ffmpeg: str = "ffmpeg",
    cwd: Path | None = None,
    total_seconds: float | None = None,
    on_progress: ProgressCb | None = None,
) -> None:
    """Lance ffmpeg ; si `on_progress` est fourni, remonte l'avancement (0..1)."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
    if on_progress and total_seconds:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += args
    log.debug("ffmpeg %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_chunks: list[bytes] = []

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            stderr_chunks.append(chunk)
            if len(stderr_chunks) > 200:
                del stderr_chunks[:100]

    async def _read_progress() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            if on_progress and total_seconds and line.startswith((b"out_time_us=", b"out_time_ms=")):
                try:
                    us = int(line.split(b"=", 1)[1].strip())
                except ValueError:
                    continue
                frac = max(0.0, min(1.0, us / 1_000_000 / total_seconds))
                res = on_progress(frac)
                if asyncio.iscoroutine(res):
                    await res

    await asyncio.gather(_drain_stderr(), _read_progress())
    code = await proc.wait()
    if code != 0:
        err = b"".join(stderr_chunks).decode("utf-8", "replace").strip().splitlines()
        tail = "\n".join(err[-12:])
        raise FFmpegError(f"ffmpeg a échoué (code {code}):\n{tail}")


async def ffprobe_json(path: Path, ffprobe: str = "ffprobe") -> dict:
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe a échoué pour {path.name}: {err.decode('utf-8', 'replace')[-300:]}")
    return json.loads(out or b"{}")


async def probe_duration(path: Path, ffprobe: str = "ffprobe") -> float:
    info = await ffprobe_json(path, ffprobe)
    dur = info.get("format", {}).get("duration")
    if dur is None:
        for s in info.get("streams", []):
            if s.get("duration"):
                dur = s["duration"]
                break
    return float(dur or 0.0)


async def probe_dimensions(path: Path, ffprobe: str = "ffprobe") -> tuple[int, int] | None:
    info = await ffprobe_json(path, ffprobe)
    for st in info.get("streams", []):
        if st.get("codec_type") == "video" and st.get("width") and st.get("height"):
            return int(st["width"]), int(st["height"])
    return None


async def probe_has_audio(path: Path, ffprobe: str = "ffprobe") -> bool:
    info = await ffprobe_json(path, ffprobe)
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


async def to_wav(src: Path, dst: Path, *, ffmpeg: str = "ffmpeg", sample_rate: int = 44100, filters: str | None = None) -> None:
    args = ["-i", str(src)]
    if filters:
        args += ["-af", filters]
    args += ["-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)]
    await run_ffmpeg(args, ffmpeg=ffmpeg)
