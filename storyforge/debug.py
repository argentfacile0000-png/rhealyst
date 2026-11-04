"""Mode débogage : la génération d'une vidéo, étape par étape, déclenchée à la main, avec journaux détaillés.

Étapes : histoire → voix → fond → sous-titres → montage → enregistrement. Chaque étape peut être relancée ;
les fichiers produits (prompts, réponse brute de l'IA, audio, image du fond, sous-titres, vidéo) sont envoyés.
"""

from __future__ import annotations

import json
import random
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import db
from .models import Story, Video, VideoFormat, VideoStatus
from .story.styles import get_language
from .tts.elevenlabs import VoiceSettings
from .video.captions import CaptionStyle, build_ass, hook_end_time
from .video.card import render_hook_card
from .video.renderer import RenderJob, grab_frame, make_telegram_preview, make_thumbnail, render

if TYPE_CHECKING:
    from .core import Services

STEPS = [("story", "Histoire"), ("voice", "Voix off"), ("background", "Séquence de fond"),
         ("captions", "Sous-titres"), ("render", "Montage"), ("save", "Enregistrement")]
LABELS = dict(STEPS)


@dataclass
class StepLog:
    status: str = "todo"  # todo | ok | error
    seconds: float = 0.0
    lines: list[str] = field(default_factory=list)
    files: list[tuple[str, Path | bytes]] = field(default_factory=list)  # (nom, chemin ou contenu)


@dataclass
class DebugSession:
    channel_id: int
    kind: str = VideoFormat.VERTICAL
    rng: random.Random = field(default_factory=random.Random)
    workdir: Path | None = None
    steps: dict[str, StepLog] = field(default_factory=lambda: {k: StepLog() for k, _ in STEPS})
    story: object | None = None  # GeneratedStory
    tts: object | None = None  # TTSResult
    seg: object | None = None  # Segment
    ass: Path | None = None
    card: tuple[Path | None, float] = (None, 0.0)
    output: Path | None = None
    video_id: int | None = None
    busy: bool = False  # une étape tourne

    @property
    def horizontal(self) -> bool:
        return self.kind == VideoFormat.HORIZONTAL

    def next_step(self) -> str | None:
        for key, _ in STEPS:
            if self.steps[key].status != "ok":
                return key
        return None

    def can_run(self, key: str) -> bool:
        order = [k for k, _ in STEPS]
        return all(self.steps[k].status == "ok" for k in order[:order.index(key)])

    def reset_from(self, key: str) -> None:
        order = [k for k, _ in STEPS]
        for k in order[order.index(key):]:
            self.steps[k] = StepLog()

    def cleanup(self) -> None:
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)


async def run_step(svc: Services, sess: DebugSession, key: str) -> StepLog:
    """Exécute une étape (et invalide les suivantes). Les erreurs sont journalisées, jamais levées."""
    sess.reset_from(key)
    log = sess.steps[key]
    ch = await svc.get_channel(sess.channel_id)
    if ch is None:
        log.status, log.lines = "error", ["Chaîne introuvable"]
        return log
    if sess.workdir is None:
        sess.workdir = svc.settings.work_dir / f"debug_{int(time.time())}_{sess.channel_id}"
    sess.workdir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        await STEP_FUNCS[key](svc, sess, ch, log)
        log.status = "ok"
    except Exception as exc:
        log.status = "error"
        log.lines.append(f"ERREUR : {type(exc).__name__}: {exc}")
        log.files.append(("erreur.txt", traceback.format_exc().encode()))
    log.seconds = round(time.monotonic() - t0, 1)
    return log


# ---------------------------------------------------------------------- étapes
async def _story(svc: Services, sess: DebugSession, ch, log: StepLog) -> None:
    gen = svc.pipeline.stories
    lo, hi = sorted((ch.min_seconds, ch.max_seconds))
    seconds = int(sess.rng.uniform(lo, hi))
    log.lines += [f"Chaîne : {ch.name} · langue {ch.language} · durée visée {seconds} s",
                  f"Prompt système : {'personnalisé' if gen.system_prompt else 'original'}"]
    try:
        gs = await gen.generate(language=ch.language, formats=ch.formats, narrator_gender=ch.narrator_gender,
                                parts=1, seconds_per_part=seconds, voice_speed=ch.voice_speed, speedup=ch.speedup,
                                custom_instructions=ch.custom_instructions, rng=sess.rng)
    finally:  # même en cas d'échec : prompts et réponses brutes
        dump = []
        for i, ex in enumerate(gen.exchanges, start=1):
            log.lines.append(f"Appel {i} : modèle {ex['model'] or '?'}, {ex['seconds'] or '?'} s, "
                             f"jetons {ex['tokens'][0]} → {ex['tokens'][1]}, fin : {ex['finish_reason']}")
            dump.append(f"===== APPEL {i} · {ex['model']} =====")
            for m in ex["messages"]:
                dump.append(f"----- {m['role'].upper()} -----\n{m['content']}")
            dump.append(f"----- RÉPONSE BRUTE -----\n{ex['reply']}\n")
        if dump:
            log.files.append(("ia_prompts_et_reponses.txt", "\n".join(dump).encode()))
    sess.story = gs
    words = sum(len(p.split()) for p in gs.parts)
    log.lines += [f"Titre : {gs.title}", f"Accroche : {gs.hook}", f"Format : {gs.format} · {words} mots",
                  f"Hashtags : {' '.join('#' + h for h in gs.hashtags)}"]
    log.files.append(("histoire.json", json.dumps({
        "title": gs.title, "hook": gs.hook, "parts": gs.parts, "youtube_title": gs.youtube_title,
        "description": gs.description, "hashtags": gs.hashtags, "tiktok_caption": gs.tiktok_caption,
        "format": gs.format, "dna": gs.dna}, ensure_ascii=False, indent=2).encode()))


async def _voice(svc: Services, sess: DebugSession, ch, log: StepLog) -> None:
    text = " ".join(sess.story.parts)
    voice = ch.voice_id or svc.settings.elevenlabs_default_voice_id
    ring = svc.pipeline.tts.keys
    log.lines.append(f"Voix : {ch.voice_name or voice} · vitesse {ch.voice_speed} · accélération {ch.speedup} · "
                     f"stabilité {ch.voice_stability}")
    tts = await svc.pipeline.tts.synthesize(
        text, voice, VoiceSettings(stability=ch.voice_stability, similarity_boost=ch.voice_similarity,
                                   style=ch.voice_style, speed=ch.voice_speed),
        sess.workdir / "voice", speedup=ch.speedup)
    sess.tts = tts
    key = next((k for k in (ring.all() if ring else []) if k.kid == ring.current), None)
    log.lines += [f"Durée : {tts.duration:.1f} s · {len(tts.words)} mots synchronisés · {tts.characters} caractères",
                  f"Clé ElevenLabs : {key.masked if key else '?'}",
                  "Premiers mots : " + ", ".join(f"{w.text}@{w.start:.2f}" for w in tts.words[:8])]
    mp3 = sess.workdir / "voix.mp3"
    proc = await _ffmpeg(svc, ["-i", str(tts.audio_path), "-c:a", "libmp3lame", "-b:a", "96k", str(mp3)])
    log.files.append(("voix.mp3", mp3 if proc == 0 and mp3.exists() else tts.audio_path))


async def _background(svc: Services, sess: DebugSession, ch, log: StepLog) -> None:
    duration = sess.tts.duration + 0.5
    speed = min(4.0, max(0.25, ch.bg_speed or 1.0))
    seg = await svc.pipeline.bank.pick(duration * speed, categories=ch.bg_categories or None,
                                       recent=await svc.pipeline._recent_segments(), rng=sess.rng,
                                       orientation="horizontal" if sess.horizontal else "vertical")
    sess.seg = seg
    meta = await svc.pipeline.bank.info(seg.path)
    log.lines += [f"Fichier : {seg.path.name} ({meta.get('orientation') or '?'})",
                  f"Catégories autorisées : {', '.join(ch.bg_categories) or 'toutes'}",
                  f"Séquence : {seg.start:.1f} s → {seg.start + duration * speed:.1f} s sur {seg.source_duration:.0f} s"
                  + (" · bouclée (fond trop court)" if seg.loop else "") + (f" · vitesse {speed:g}×" if speed != 1 else "")]
    frame = await grab_frame(seg.path, sess.workdir / "fond.jpg", svc.settings, at=seg.start + min(2.0, duration / 3))
    log.files.append(("fond.jpg", frame))


async def _captions(svc: Services, sess: DebugSession, ch, log: StepLog) -> None:
    W, H = svc.settings.video_width, svc.settings.video_height
    if sess.horizontal:
        W, H = max(W, H), min(W, H)
    duration = sess.tts.duration + 0.5
    card_png, card_until = None, 0.0
    if ch.hook_card and sess.story.hook:
        card_png = render_hook_card(sess.story.hook, ch.name, sess.workdir / "card.png", svc.settings.fonts_dir,
                                    width=min(W, H))
        card_until = hook_end_time(sess.tts.words, sess.story.hook)
        log.files.append(("carte_accroche.png", card_png))
    sess.card = (card_png, card_until)
    base = 92 if ch.caption_style == "trio" else 112
    style = CaptionStyle(mode=ch.caption_style, color=ch.caption_color, highlight=ch.highlight_color,
                         position=ch.caption_position,
                         size=int(base * min(W, H) / 1080 * (0.85 if sess.horizontal else 1.0)))
    content = build_ass(sess.tts.words, width=W, height=H, style=style, label=None, duration=duration,
                        captions_from=card_until)
    sess.ass = sess.workdir / "subs.ass"
    sess.ass.write_text(content, "utf-8")
    events = sum(1 for line in content.splitlines() if line.startswith("Dialogue:"))
    log.lines += [f"Format : {W}×{H} · style {ch.caption_style} · police {style.size} px · position {ch.caption_position} %",
                  f"{events} sous-titres" + (f" · carte d'accroche jusqu'à {card_until:.1f} s" if card_png else "")]
    log.files.append(("sous-titres.ass", sess.ass))


async def _render(svc: Services, sess: DebugSession, ch, log: StepLog) -> None:
    W, H = svc.settings.video_width, svc.settings.video_height
    if sess.horizontal:
        W, H = max(W, H), min(W, H)
    duration = sess.tts.duration + 0.5
    music = await svc.pipeline.music.pick(ch.music_tracks, duration, sess.rng) if ch.music_volume > 0 else None
    sess.output = sess.workdir / "video.mp4"
    card_png, card_until = sess.card
    await render(RenderJob(segment=sess.seg, narration=sess.tts.audio_path, ass_file=sess.ass, output=sess.output,
                           duration=duration, card_png=card_png, card_until=card_until,
                           music=music.path if music else None, music_start=music.start if music else 0.0,
                           music_volume=ch.music_volume, bg_volume=ch.bg_volume, bg_speed=ch.bg_speed or 1.0,
                           width=W, height=H), svc.settings)
    size = sess.output.stat().st_size / 1e6
    log.lines += [f"Vidéo : {W}×{H} · {duration:.1f} s · {size:.1f} Mo · CRF {svc.settings.video_crf} · "
                  f"preset {svc.settings.video_preset}",
                  "Bande son : " + (f"{music.name} à partir de {music.start:.0f} s, volume {ch.music_volume:g}"
                                    if music else "aucune")]
    preview = await make_telegram_preview(sess.output, sess.workdir / "apercu.mp4", svc.settings, duration=duration)
    log.files.append(("video.mp4", preview))


async def _save(svc: Services, sess: DebugSession, ch, log: StepLog) -> None:
    """Enregistre la vidéo de test comme vraie vidéo, en attente de validation (publiable ensuite)."""
    gs = sess.story
    out_dir = svc.settings.output_dir / f"{ch.id}-debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    async with db.session() as s:
        story = Story(channel_id=ch.id, format=gs.format, language=gs.language, title=gs.title, hook=gs.hook,
                      parts=gs.parts, youtube_title=gs.youtube_title, description=gs.description,
                      hashtags=gs.hashtags, tiktok_caption=gs.tiktok_caption, model=gs.model,
                      prompt_tokens=gs.prompt_tokens, completion_tokens=gs.completion_tokens)
        s.add(story)
        await s.flush()
        video = Video(channel_id=ch.id, story_id=story.id, part_index=1, part_count=1, format=sess.kind,
                      status=VideoStatus.RENDERING, title=gs.title, text=" ".join(gs.parts))
        s.add(video)
        await s.flush()
        vid = video.id
    final = out_dir / f"{vid:05d}-debug.mp4"
    shutil.copy2(sess.output, final)
    thumb = await make_thumbnail(final, final.with_suffix(".jpg"), svc.settings, at=1.0)
    preview = await make_telegram_preview(final, final.with_name(final.stem + "_preview.mp4"), svc.settings,
                                          duration=sess.tts.duration)
    async with db.session() as s:
        v = await s.get(Video, vid)
        v.file_path, v.thumb_path, v.preview_path = str(final), str(thumb), str(preview)
        v.duration = sess.tts.duration + 0.5
        v.file_size = final.stat().st_size
        v.bg_file, v.bg_start = str(sess.seg.path), sess.seg.start
        v.tts_chars = sess.tts.characters
        v.words = [[w.text, round(w.start, 3), round(w.end, 3)] for w in sess.tts.words]
        v.status = VideoStatus.PENDING_APPROVAL
    sess.video_id = vid
    await db.log_event(f"{ch.name} : vidéo #{vid} enregistrée depuis le mode débogage", "info", ch.id)
    log.lines.append(f"Vidéo #{vid} enregistrée, en attente de validation (publiable depuis Vidéos).")


async def _ffmpeg(svc: Services, args: list[str]) -> int:
    import asyncio

    proc = await asyncio.create_subprocess_exec(svc.settings.ffmpeg_bin, "-v", "error", "-y", *args,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return await proc.wait()


STEP_FUNCS = {"story": _story, "voice": _voice, "background": _background, "captions": _captions,
              "render": _render, "save": _save}


def lang_label(code: str) -> str:
    return get_language(code).label
