"""Rendu de démonstration 100 % hors-ligne (sans clé API) pour vérifier le montage.

Usage : python -m storyforge demo [--style pop|trio] [--card]
Génère un fond synthétique, une narration muette horodatée et la vidéo finale
dans data/demo/. Pratique pour régler les sous-titres et vérifier ffmpeg.
"""

from __future__ import annotations

import random
from pathlib import Path

from .config import Settings
from .story.styles import get_language
from .tts.timing import Word
from .video.background import Segment
from .video.captions import CaptionStyle, build_ass, hook_end_time
from .video.card import render_hook_card
from .video.ffmpeg import run_ffmpeg
from .video.renderer import RenderJob, make_thumbnail, render

SAMPLE = (
    "Mon coloc a vendu mon canapé pendant que j'étais en vacances, et il a eu le culot de me demander ma part. "
    "J'avais 24 ans, je bossais comme barista dans un café où le latte coûte sept euros. "
    "Pas un message. Pas un appel. Juste une photo de mon salon vide, avec une plante au milieu. "
    "Il a dit, franchement, tu t'en servais jamais. "
    "Je l'ai regardé comme un contrôleur des impôts regarde une facture en liquide."
)


async def run_demo(settings: Settings, *, style: str = "pop", card: bool = False, seconds: float | None = None) -> Path:
    out_dir = settings.data_dir / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)

    # Mots horodatés à ~4 mots/s (rythme des vidéos storytime)
    words: list[Word] = []
    t = 0.15
    for raw in SAMPLE.split():
        d = 0.16 + 0.03 * len(raw.strip(".,!?")) + rng.uniform(-0.03, 0.03)
        words.append(Word(raw, t, t + d))
        t += d + (0.18 if raw.endswith((".", "?", "!")) else 0.04)
    duration = seconds or (words[-1].end + 0.6)

    bg = out_dir / "demo_background.mp4"
    if not bg.exists():
        await run_ffmpeg(
            ["-f", "lavfi", "-i", "gradients=s=1280x720:speed=0.015:c0=0xff7e5f:c1=0x2b5876:c2=0x4e4376:c3=0xfeb47b:n=4",
             "-t", "180", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(bg)],
            ffmpeg=settings.ffmpeg_bin,
        )
    narration = out_dir / "demo_narration.wav"
    await run_ffmpeg(
        ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{duration:.2f}", "-c:a", "pcm_s16le", str(narration)],
        ffmpeg=settings.ffmpeg_bin,
    )

    start = rng.uniform(5, 180 - duration - 5)
    seg = Segment(bg, start, duration, 180.0)

    card_png = None
    card_until = 0.0
    hook = SAMPLE.split(". ")[0] + "."
    if card:
        card_png = render_hook_card(hook, "Histoires Folles", out_dir / "card.png", settings.fonts_dir)
        card_until = hook_end_time(words, hook)

    lang = get_language("fr")
    ass = out_dir / "subs.ass"
    ass.write_text(
        build_ass(
            words,
            width=settings.video_width,
            height=settings.video_height,
            style=CaptionStyle(mode=style),
            label=f"{lang.part_word} 1",
            duration=duration,
            captions_from=card_until,
        ),
        "utf-8",
    )
    out = out_dir / f"demo_{style}{'_card' if card else ''}.mp4"
    await render(
        RenderJob(segment=seg, narration=narration, ass_file=ass, output=out, duration=duration,
                  card_png=card_png, card_until=card_until),
        settings,
    )
    await make_thumbnail(out, out_dir / f"{out.stem}.jpg", settings, at=min(3.0, duration / 2))
    return out
