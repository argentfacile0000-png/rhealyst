"""Découpage d'une vidéo en parties de durée maximale, coupées en fin de phrase."""

from __future__ import annotations

import math
from pathlib import Path

from ..config import Settings
from .captions import CaptionStyle, build_ass
from .ffmpeg import run_ffmpeg
from .renderer import _link_fonts

SENTENCE_END = (".", "!", "?", "…", "!»", "?»", ".»", '."', '!"', '?"')
MIN_PART = 8.0  # une partie ne fait jamais moins de 8 s (sauf vidéo très courte)


def _boundaries(words: list[list], total: float) -> tuple[list[float], list[float]]:
    """Points de coupe possibles : (fins de phrase, simples pauses entre deux mots)."""
    sentences, gaps = [], []
    for (text, _, end), (_, nxt_start, _) in zip(words, words[1:]):
        cut = (float(end) + float(nxt_start)) / 2
        if 0 < cut < total:
            gaps.append(cut)
            if str(text).rstrip().endswith(SENTENCE_END):
                sentences.append(cut)
    return sentences, gaps


def plan_cuts(words: list[list] | None, total: float, max_seconds: float) -> list[tuple[float, float]]:
    """Segments [(début, fin)] de longueur ≤ max_seconds, équilibrés, coupés en fin de phrase si possible."""
    if total <= max_seconds or max_seconds <= 0:
        return [(0.0, total)]
    sentences, gaps = _boundaries(words or [], total)
    segments: list[tuple[float, float]] = []
    start = 0.0
    while total - start > max_seconds:
        remaining = total - start
        n = math.ceil(remaining / max_seconds)
        ideal = start + remaining / n
        lo, hi = start + min(MIN_PART, max_seconds / 2), start + max_seconds
        cut = None
        for pool in (sentences, gaps):
            candidates = [c for c in pool if lo <= c <= hi]
            if candidates:
                cut = min(candidates, key=lambda c: abs(c - ideal))
                break
        if cut is None:  # aucun silence exploitable : coupe franche
            cut = min(ideal, hi)
        segments.append((start, cut))
        start = cut
    segments.append((start, total))
    return segments


def words_between(words: list[list] | None, start: float, end: float) -> list[list]:
    """Mots d'un segment, recalés sur le début de la partie."""
    out = []
    for text, s, e in words or []:
        mid = (float(s) + float(e)) / 2
        if start <= mid < end:
            out.append([text, round(float(s) - start, 3), round(float(e) - start, 3)])
    return out


async def cut_part(src: Path, out: Path, start: float, end: float, label: str | None, *,
                   width: int, height: int, workdir: Path, settings: Settings) -> Path:
    """Extrait [start, end] et incruste le label « PARTIE N » en haut."""
    workdir.mkdir(parents=True, exist_ok=True)
    duration = max(0.5, end - start)
    fonts = _link_fonts(workdir, settings.fonts_dir)
    ass = workdir / f"label_{out.stem}.ass"
    ass.write_text(build_ass([], width=width, height=height, style=CaptionStyle(), label=label, duration=duration), "utf-8")
    fade = min(0.08, duration / 4)
    args = [
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src.resolve()),
        "-vf", f"ass={ass.name}:fontsdir={fonts}" if label else "null",
        "-af", f"afade=t=in:d={fade:.2f},afade=t=out:st={max(0.0, duration - fade):.2f}:d={fade:.2f}",
        "-c:v", "libx264", "-preset", settings.video_preset, "-crf", str(settings.video_crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(settings.video_fps),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", "-use_editlist", "0",
        str(out.resolve()),
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    await run_ffmpeg(args, ffmpeg=settings.ffmpeg_bin, cwd=workdir)
    return out
