"""Montage final : fond recadré 9:16 + narration + sous-titres mot à mot (+ carte, musique)."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from .background import Segment
from .ffmpeg import ProgressCb, probe_has_audio, run_ffmpeg

log = logging.getLogger(__name__)

TELEGRAM_MAX_BYTES = 48 * 1024 * 1024


@dataclass
class RenderJob:
    segment: Segment
    narration: Path
    ass_file: Path
    output: Path
    duration: float
    card_png: Path | None = None
    card_until: float = 0.0
    music: Path | None = None
    music_volume: float = 0.0
    music_start: float = 0.0  # départ dans la piste (s)
    bg_volume: float = 0.0
    width: int = 0  # 0 = réglages (vertical) ; 1920×1080 pour une vidéo longue
    height: int = 0


def _link_fonts(workdir: Path, fonts_dir: Path) -> str:
    """Expose le dossier de polices dans le dossier de travail (chemins relatifs = pas d'échappement)."""
    target = workdir / "fonts"
    if not target.exists():
        try:
            os.symlink(fonts_dir.resolve(), target, target_is_directory=True)
        except OSError:
            shutil.copytree(fonts_dir, target, dirs_exist_ok=True)
    return "fonts"


async def render(job: RenderJob, settings: Settings, on_progress: ProgressCb | None = None) -> Path:
    W, H, fps = job.width or settings.video_width, job.height or settings.video_height, settings.video_fps
    workdir = job.ass_file.parent
    fonts_rel = _link_fonts(workdir, settings.fonts_dir)
    seg = job.segment

    args: list[str] = []
    # 0: fond (recherche rapide avant -i), bouclé si trop court
    if seg.loop:
        args += ["-stream_loop", "-1"]
    else:
        args += ["-ss", f"{seg.start:.3f}"]
    args += ["-t", f"{job.duration + 0.5:.3f}", "-i", str(seg.path.resolve())]
    # 1: narration
    args += ["-i", str(job.narration.resolve())]
    inputs = 2
    card_idx = music_idx = None
    if job.card_png and job.card_until > 0:
        args += ["-i", str(job.card_png.resolve())]
        card_idx = inputs
        inputs += 1
    if job.music and job.music_volume > 0:
        args += ["-stream_loop", "-1"]
        if job.music_start > 0:
            args += ["-ss", f"{job.music_start:.3f}"]
        args += ["-i", str(job.music.resolve())]
        music_idx = inputs
        inputs += 1

    vf = [
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={fps},format=yuv420p[bg]"
    ]
    last = "bg"
    if card_idx is not None:
        vf.append(
            f"[{card_idx}:v]format=rgba[card];[{last}][card]overlay=(W-w)/2:(H-h)/2:"
            f"enable='between(t,0,{job.card_until:.2f})'[vc]"
        )
        last = "vc"
    vf.append(f"[{last}]ass={job.ass_file.name}:fontsdir={fonts_rel}[v]")

    af = ["[1:a]aresample=44100,aformat=channel_layouts=stereo[narr]"]
    mix = ["[narr]"]
    if job.bg_volume > 0 and await probe_has_audio(seg.path):
        af.append(f"[0:a]aresample=44100,aformat=channel_layouts=stereo,volume={job.bg_volume:.3f}[bga]")
        mix.append("[bga]")
    if music_idx is not None:
        # Fondu d'entrée et de sortie : la bande son ne démarre ni ne s'arrête brutalement
        fade_out = max(0.0, job.duration - 2.5)
        af.append(
            f"[{music_idx}:a]aresample=44100,aformat=channel_layouts=stereo,volume={job.music_volume:.3f},"
            f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out:.3f}:d=2.5[mus]"
        )
        mix.append("[mus]")
    if len(mix) > 1:
        af.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]")
    else:
        af.append("[narr]anull[a]")

    args += [
        "-filter_complex", ";".join(vf + af),
        "-map", "[v]", "-map", "[a]",
        "-t", f"{job.duration:.3f}",
        "-c:v", "libx264", "-preset", settings.video_preset, "-crf", str(settings.video_crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps),
        # Compatible Instagram/TikTok/YouTube : AAC 128 kb/s, moov en tête, sans liste d'édition
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", "-use_editlist", "0",
        str(job.output.resolve()),
    ]
    job.output.parent.mkdir(parents=True, exist_ok=True)
    await run_ffmpeg(args, ffmpeg=settings.ffmpeg_bin, cwd=workdir, total_seconds=job.duration, on_progress=on_progress)
    return job.output


async def make_thumbnail(video: Path, out: Path, settings: Settings, at: float = 1.0) -> Path:
    await run_ffmpeg(
        ["-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)],
        ffmpeg=settings.ffmpeg_bin,
    )
    return out


async def make_telegram_preview(video: Path, out: Path, settings: Settings, *, duration: float = 0.0) -> Path:
    """Version allégée (< 50 Mo) pour l'aperçu dans Telegram. Vidéo longue : les 90 premières secondes."""
    long_video = duration > 180
    if video.stat().st_size <= TELEGRAM_MAX_BYTES and not long_video:
        return video
    args = ["-i", str(video)]
    if long_video:
        args += ["-t", "90"]
    args += ["-vf", "scale='min(960,iw)':-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)]
    await run_ffmpeg(args, ffmpeg=settings.ffmpeg_bin)
    return out


async def grab_frame(video: Path, out: Path, settings: Settings, at: float) -> Path:
    await run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)],
                     ffmpeg=settings.ffmpeg_bin)
    return out
