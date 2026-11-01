"""Chaîne complète : histoire -> voix -> fond aléatoire -> montage -> (validation) -> publication."""

from __future__ import annotations

import logging
import random
import re
import shutil
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select

from . import db
from .config import Settings
from .models import Channel, Publication, PubStatus, Story, Video, VideoStatus, utcnow
from .notify import Notifier, Progress
from .publishers import PublisherError, PublishRequest, tiktok, youtube
from .story import StoryGenerator
from .story.styles import FORMATS, get_language
from .tts import ElevenLabsClient, VoiceSettings
from .video.background import BackgroundBank
from .video.captions import CaptionStyle, build_ass, hook_end_time
from .video.card import render_hook_card
from .video.renderer import RenderJob, make_telegram_preview, make_thumbnail, render

log = logging.getLogger(__name__)


def slugify(text: str, max_len: int = 40) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return (t or "video")[:max_len].strip("-")


def fmt_ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Throttle:
    """Limite la fréquence des mises à jour de progression (API Telegram)."""

    def __init__(self, interval: float = 2.5):
        self.interval = interval
        self.last = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self.last >= self.interval:
            self.last = now
            return True
        return False


class Pipeline:
    def __init__(self, settings: Settings, notifier: Notifier):
        self.settings = settings
        self.notifier = notifier
        self.stories = StoryGenerator(settings)
        self.tts = ElevenLabsClient(settings.elevenlabs_api_key, settings.elevenlabs_model, ffmpeg=settings.ffmpeg_bin)
        self.bank = BackgroundBank(settings.backgrounds_dir, ffprobe=settings.ffprobe_bin)
        # Branché par Services : met une publication en file d'attente.
        self.enqueue_publish: Callable[[int], object] | None = None

    # ------------------------------------------------------------------ génération
    async def generate(self, channel_id: int, *, publish_after: bool = False, progress: Progress | None = None) -> list[int]:
        async with db.session() as s:
            ch = await s.get(Channel, channel_id)
            if ch is None:
                raise ValueError(f"Chaîne {channel_id} introuvable")
            titles = (
                await s.execute(
                    select(Story.title).where(Story.channel_id == channel_id).order_by(Story.id.desc()).limit(30)
                )
            ).scalars().all()
        progress = progress or await self.notifier.progress(f"Génération · {ch.name}")
        rng = random.Random()
        video_ids: list[int] = []

        try:
            await progress.update("story", "run")
            seconds = rng.uniform(min(ch.min_seconds, ch.max_seconds), max(ch.min_seconds, ch.max_seconds))
            gs = await self.stories.generate(
                language=ch.language,
                formats=ch.formats,
                narrator_gender=ch.narrator_gender,
                parts=ch.parts,
                seconds_per_part=int(seconds),
                voice_speed=ch.voice_speed,
                speedup=ch.speedup,
                custom_instructions=ch.custom_instructions,
                avoid_titles=list(titles),
                rng=rng,
            )
            async with db.session() as s:
                story = Story(
                    channel_id=ch.id, format=gs.format, language=gs.language, title=gs.title, hook=gs.hook,
                    parts=gs.parts, youtube_title=gs.youtube_title, description=gs.description,
                    hashtags=gs.hashtags, tiktok_caption=gs.tiktok_caption, model=gs.model,
                    prompt_tokens=gs.prompt_tokens, completion_tokens=gs.completion_tokens,
                )
                s.add(story)
                await s.flush()
                story_id = story.id
            fmt = FORMATS.get(gs.format)
            await progress.update(
                "story", "done",
                detail=f"{fmt.emoji if fmt else ''} « {gs.title[:70]} » · {gs.word_count} mots",
            )

            for i, text in enumerate(gs.parts, start=1):
                vid = await self._render_part(ch, story_id, gs.title, gs.hook, i, len(gs.parts), text, progress, rng)
                video_ids.append(vid)
        except Exception as exc:
            # Une série incomplète ne doit pas être publiée : les parties déjà montées passent en échec.
            if video_ids:
                async with db.session() as s:
                    for vid in video_ids:
                        v = await s.get(Video, vid)
                        if v:
                            v.status = VideoStatus.FAILED
                            v.error = "Une autre partie de l'histoire a échoué"
            await progress.finish(f"❌ Échec : {exc}", ok=False)
            await db.log_event(f"Génération échouée ({ch.name}) : {exc}", "error", ch.id)
            raise

        status = VideoStatus.PENDING_APPROVAL if ch.approval_required else VideoStatus.READY
        async with db.session() as s:
            for vid in video_ids:
                v = await s.get(Video, vid)
                if v:
                    v.status = status
                    v.publish_after = publish_after and vid == video_ids[0] and not ch.approval_required
        n = len(video_ids)
        await progress.finish(
            f"✅ {n} vidéo{'s' if n > 1 else ''} prête{'s' if n > 1 else ''}"
            + (" · en attente de validation" if ch.approval_required else "")
        )
        await db.log_event(f"🎬 {ch.name} : « {gs.title[:80]} » ({n} partie{'s' if n > 1 else ''})", "success", ch.id)

        if ch.approval_required:
            for vid in video_ids:
                await self.notifier.video_for_approval(vid)
        elif publish_after and self.enqueue_publish:
            self.enqueue_publish(video_ids[0])
        return video_ids

    async def _recent_segments(self) -> list[tuple[str, float, float]]:
        async with db.session() as s:
            rows = (
                await s.execute(
                    select(Video.bg_file, Video.bg_start, Video.duration)
                    .where(Video.bg_file != "")
                    .order_by(Video.id.desc())
                    .limit(300)
                )
            ).all()
        return [(f, a, a + d) for f, a, d in rows]

    async def _render_part(
        self, ch: Channel, story_id: int, title: str, hook: str, index: int, count: int, text: str,
        progress: Progress, rng: random.Random,
    ) -> int:
        lang = get_language(ch.language)
        part_txt = f" · partie {index}/{count}" if count > 1 else ""
        async with db.session() as s:
            video = Video(
                channel_id=ch.id, story_id=story_id, part_index=index, part_count=count,
                status=VideoStatus.RENDERING, title=title, text=text,
            )
            s.add(video)
            await s.flush()
            video_id = video.id

        workdir = self.settings.work_dir / f"video_{video_id}"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            # 1) Voix
            await progress.update("voice", "run", detail=part_txt.strip(" ·"))
            tts = await self.tts.synthesize(
                text,
                ch.voice_id or self.settings.elevenlabs_default_voice_id,
                VoiceSettings(
                    stability=ch.voice_stability, similarity_boost=ch.voice_similarity,
                    style=ch.voice_style, speed=ch.voice_speed,
                ),
                workdir,
                speedup=ch.speedup,
            )
            duration = tts.duration + 0.5
            await progress.update("voice", "done", detail=f"{fmt_ts(tts.duration)} de narration{part_txt}")

            # 2) Fond : vidéo au hasard + séquence au hasard qui couvre toute la durée
            await progress.update("background", "run")
            seg = await self.bank.pick(
                duration, categories=ch.bg_categories or None, recent=await self._recent_segments(), rng=rng
            )
            rel = seg.path.relative_to(self.settings.backgrounds_dir) if seg.path.is_relative_to(self.settings.backgrounds_dir) else seg.path.name
            await progress.update(
                "background", "done",
                detail=f"{rel} · {fmt_ts(seg.start)} → {fmt_ts(seg.start + duration)} (sur {fmt_ts(seg.source_duration)})",
            )

            # 3) Sous-titres + carte d'accroche
            card_png, card_until = None, 0.0
            if ch.hook_card and index == 1 and hook:
                card_png = render_hook_card(hook, ch.name, workdir / "card.png", self.settings.fonts_dir,
                                            width=self.settings.video_width)
                card_until = hook_end_time(tts.words, hook)
            label = f"{lang.part_word} {index}" if (ch.show_part_label and count > 1) else None
            style = CaptionStyle(
                mode=ch.caption_style, color=ch.caption_color, highlight=ch.highlight_color,
                position=ch.caption_position,
            )
            ass = workdir / "subs.ass"
            ass.write_text(
                build_ass(tts.words, width=self.settings.video_width, height=self.settings.video_height,
                          style=style, label=label, duration=duration, captions_from=card_until),
                "utf-8",
            )

            # 4) Montage
            out_dir = self.settings.output_dir / f"{ch.id}-{slugify(ch.name, 24)}"
            out = out_dir / f"{video_id:05d}-{slugify(title)}{f'-p{index}' if count > 1 else ''}.mp4"
            throttle = Throttle()

            async def on_progress(frac: float) -> None:
                if throttle.ready():
                    await progress.update("render", "run", pct=frac, detail=part_txt.strip(" ·"))

            await progress.update("render", "run", pct=0.0, detail=part_txt.strip(" ·"))
            music = self.bank.random_music(self.settings.music_dir, rng) if ch.music_volume > 0 else None
            await render(
                RenderJob(
                    segment=seg, narration=tts.audio_path, ass_file=ass, output=out, duration=duration,
                    card_png=card_png, card_until=card_until, music=music,
                    music_volume=ch.music_volume, bg_volume=ch.bg_volume,
                ),
                self.settings,
                on_progress=on_progress,
            )
            await progress.update("render", "done", detail=f"1080×1920 · {fmt_ts(duration)}{part_txt}")

            # 5) Miniature + aperçu Telegram
            await progress.update("finish", "run")
            thumb = await make_thumbnail(out, out.with_suffix(".jpg"), self.settings, at=min(2.0, duration / 3))
            preview = await make_telegram_preview(out, out.with_name(out.stem + "_preview.mp4"), self.settings)
            await progress.update("finish", "done")

            async with db.session() as s:
                v = await s.get(Video, video_id)
                v.file_path = str(out)
                v.thumb_path = str(thumb)
                v.preview_path = str(preview)
                v.duration = duration
                v.file_size = out.stat().st_size
                v.bg_file = str(seg.path)
                v.bg_start = seg.start
                v.tts_chars = tts.characters
            shutil.rmtree(workdir, ignore_errors=True)
            return video_id
        except Exception as exc:
            async with db.session() as s:
                v = await s.get(Video, video_id)
                if v:
                    v.status = VideoStatus.FAILED
                    v.error = str(exc)[:1000]
            raise

    # ------------------------------------------------------------------ publication
    async def publish(self, video_id: int) -> None:
        async with db.session() as s:
            video = await s.get(Video, video_id)
            if video is None:
                raise ValueError("Vidéo introuvable")
            ch = await s.get(Channel, video.channel_id)
            story = await s.get(Story, video.story_id)
            if not video.file_path or not Path(video.file_path).exists():
                video.status = VideoStatus.FAILED
                video.error = "Fichier vidéo introuvable"
                await db.log_event(f"Publication impossible (#{video_id}) : fichier manquant", "error", video.channel_id)
                return
            platforms = ch.platforms()
            if not platforms:
                await self.notifier.notify(
                    f"⚠️ <b>{ch.name}</b> : aucune plateforme liée/activée, la vidéo #{video_id} reste en file.",
                    "warning", ch.id,
                )
                return
            video.status = VideoStatus.PUBLISHING

        lang = get_language(story.language)
        part = ""
        if video.part_count > 1:
            part = f"{lang.part_word.capitalize()} {video.part_index}"
        yt_title = story.youtube_title or story.title
        if part:
            yt_title = f"{yt_title} ({part})"
        caption = story.tiktok_caption or story.title
        if part:
            caption = f"{part} 👇 {caption}"
        description = story.description
        if part:
            description = f"{part}/{video.part_count} — {description}"

        any_ok = False
        for platform in platforms:
            async with db.session() as s:
                pub = (
                    await s.execute(
                        select(Publication).where(Publication.video_id == video_id, Publication.platform == platform)
                    )
                ).scalar_one_or_none()
                if pub and pub.status in PubStatus.OK:
                    any_ok = True
                    continue
                if pub is None:
                    pub = Publication(video_id=video_id, channel_id=ch.id, platform=platform)
                    s.add(pub)
                pub.status = PubStatus.UPLOADING
                pub.attempts = (pub.attempts or 0) + 1
                pub.error = ""
                await s.flush()
                pub_id = pub.id

            req = PublishRequest(
                video_path=video.file_path, title=yt_title, description=description, tags=story.hashtags,
                caption=caption, language=story.language,
                privacy=ch.youtube_privacy if platform == "youtube" else ch.tiktok_privacy,
                mode=ch.tiktok_mode,
            )
            try:
                if platform == "youtube":
                    result = await youtube.upload(ch.youtube_credentials or {}, req)
                    if result.credentials:
                        await self._save_creds(ch.id, "youtube_credentials", result.credentials)
                else:
                    creds, changed = await tiktok.refresh_if_needed(
                        self.settings.tiktok_client_key, self.settings.tiktok_client_secret, ch.tiktok_credentials or {}
                    )
                    if changed:
                        await self._save_creds(ch.id, "tiktok_credentials", creds)
                    result = await tiktok.publish(creds, req, username=ch.tiktok_account.lstrip("@"))
                async with db.session() as s:
                    pub = await s.get(Publication, pub_id)
                    pub.status = result.status if result.status in (PubStatus.PUBLISHED, PubStatus.DRAFT) else PubStatus.PROCESSING
                    pub.remote_id = result.remote_id
                    pub.url = result.url
                    pub.published_at = utcnow()
                any_ok = True
            except Exception as exc:
                msg = str(exc) if isinstance(exc, PublisherError) else f"{type(exc).__name__}: {exc}"
                log.exception("Publication %s échouée", platform)
                async with db.session() as s:
                    pub = await s.get(Publication, pub_id)
                    pub.status = PubStatus.FAILED
                    pub.error = msg[:1000]
                await db.log_event(f"❌ {ch.name} · {platform} : {msg}", "error", ch.id)

        async with db.session() as s:
            v = await s.get(Video, video_id)
            v.status = VideoStatus.PUBLISHED if any_ok else VideoStatus.FAILED
            if any_ok:
                v.published_at = utcnow()
                v.error = ""
            else:
                v.error = "Toutes les publications ont échoué"
        if any_ok:
            await db.log_event(f"🚀 {ch.name} : « {story.title[:70]} » publiée", "success", ch.id)
        await self.notifier.video_published(video_id)

    async def _save_creds(self, channel_id: int, field: str, creds: dict) -> None:
        async with db.session() as s:
            ch = await s.get(Channel, channel_id)
            if ch:
                setattr(ch, field, dict(creds))
