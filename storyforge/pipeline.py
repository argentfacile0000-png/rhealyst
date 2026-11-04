"""Chaîne complète : histoire -> voix -> fond aléatoire -> montage -> (validation) -> publication."""

from __future__ import annotations

import dataclasses
import html
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
from .models import Channel, Publication, PubStatus, Story, Video, VideoFormat, VideoStatus, utcnow
from .notify import Notifier, Progress
from .publishers import PLATFORM_LABELS, PublisherError, PublishRequest, instagram, tiktok, youtube
from .story import StoryGenerator
from .story.styles import FORMATS, get_language
from .tts import ElevenLabsClient, VoiceSettings
from .tts.keys import KeyRing
from .video.background import BackgroundBank
from .video.captions import CaptionStyle, build_ass, hook_end_time
from .video.card import render_cover, render_hook_card
from .video.ffmpeg import probe_dimensions
from .story.llm_keys import LLMKeyRing
from .video.music import MusicBank
from .video.renderer import RenderJob, grab_frame, make_telegram_preview, make_thumbnail, render
from .video.split import cut_part, plan_cuts, words_between

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
        self.llm_keys = LLMKeyRing(settings)
        self.stories = StoryGenerator(settings, keys=self.llm_keys)
        self.stories.on_event = self._ai_event
        self.tts = ElevenLabsClient(settings.elevenlabs_api_key, settings.elevenlabs_model, ffmpeg=settings.ffmpeg_bin,
                                    keys=KeyRing(settings))
        self.tts.on_event = self._tts_event
        self.bank = BackgroundBank(settings.backgrounds_dir, ffprobe=settings.ffprobe_bin)
        self.music = MusicBank(settings.music_dir, ffprobe=settings.ffprobe_bin)
        # Branchés par Services : file de publication et adresse publique signée d'une vidéo.
        self.enqueue_publish: Callable[[int], object] | None = None
        self.public_media_url: Callable[[int], str] | None = None

    # ------------------------------------------------------------------ génération
    async def generate(
        self, channel_id: int, *, kind: str = VideoFormat.VERTICAL, publish_after: bool = False,
        progress: Progress | None = None,
    ) -> list[int]:
        async with db.session() as s:
            ch = await s.get(Channel, channel_id)
            if ch is None:
                raise ValueError(f"Chaîne {channel_id} introuvable")
            titles = (
                await s.execute(
                    select(Story.title).where(Story.channel_id == channel_id).order_by(Story.id.desc()).limit(30)
                )
            ).scalars().all()
        # La vidéo longue YouTube est la version complète (16:9, sans découpage) de l'histoire des Shorts :
        # même histoire, même voix, durée = celle de l'histoire entière.
        long_only = kind == VideoFormat.HORIZONTAL
        with_long = long_only or (ch.long_enabled and kind == VideoFormat.VERTICAL)
        progress = progress or await self.notifier.progress(
            f"{'Vidéo longue' if long_only else 'Génération'} · {ch.name}")
        rng = random.Random()
        video_ids: list[int] = []
        long_ids: list[int] = []

        try:
            await progress.update("story", "run")
            lo, hi = sorted((ch.min_seconds, ch.max_seconds))
            seconds = rng.uniform(lo, hi)
            gs = await self.stories.generate(
                language=ch.language,
                formats=ch.formats,
                narrator_gender=ch.narrator_gender,
                parts=1 if long_only else ch.parts,
                long_form=False,
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

            reuse: dict = {}  # voix de l'histoire complète, réutilisée pour la version longue
            if not long_only:
                for i, text in enumerate(gs.parts, start=1):
                    vid = await self._render_part(ch, story_id, gs.title, gs.hook, i, len(gs.parts), text, progress,
                                                  rng, kind=kind, reuse=reuse if len(gs.parts) == 1 else None)
                    video_ids.append(vid)
            if with_long:
                try:
                    long_ids.append(await self._render_part(
                        ch, story_id, gs.title, gs.hook, 1, 1, " ".join(gs.parts), progress, rng,
                        kind=VideoFormat.HORIZONTAL, reuse=reuse,
                    ))
                except Exception as exc:
                    if long_only:
                        raise
                    log.exception("Version longue échouée")  # les Shorts restent utilisables
                    await db.log_event(f"{ch.name} : version longue de « {gs.title[:60]} » échouée : {exc}",
                                       "warning", ch.id)
                finally:
                    keep = reuse.get("dir")
                    if keep:
                        shutil.rmtree(keep, ignore_errors=True)
            # Découpage auto des Shorts trop longs en parties (Partie 1, 2…)
            if not long_only and len(video_ids) == 1 and ch.split_seconds:
                v = await self._get_video(video_ids[0])
                if v and v.duration > ch.split_seconds + 2:
                    await progress.update("finish", "run", detail=f"découpage en parties de {ch.split_seconds} s max")
                    video_ids = await self.split_video(video_ids[0], ch.split_seconds, status=VideoStatus.RENDERING)
                    await progress.update("finish", "done", detail=f"{len(video_ids)} parties")
        except Exception as exc:
            # Une série incomplète ne doit pas être publiée : les parties déjà montées passent en échec.
            if video_ids or long_ids:
                async with db.session() as s:
                    for vid in video_ids + long_ids:
                        v = await s.get(Video, vid)
                        if v:
                            v.status = VideoStatus.FAILED
                            v.error = "Une autre partie de l'histoire a échoué"
            await progress.finish(f"Échec : {exc}", ok=False)
            await db.log_event(f"Génération échouée ({ch.name}) : {exc}", "error", ch.id)
            raise

        status = VideoStatus.PENDING_APPROVAL if ch.approval_required else VideoStatus.READY
        first = (video_ids or long_ids)[0]
        async with db.session() as s:
            for vid in video_ids + long_ids:
                v = await s.get(Video, vid)
                if v:
                    v.status = status
                    v.publish_after = publish_after and vid == first and not ch.approval_required
        n = len(video_ids)
        done = [f"{n} Short{'s' if n > 1 else ''}"] if n else []
        if long_ids:
            done.append("version longue")
        await progress.finish(
            (" + ".join(done) or "Rien") + " prêt" + ("s" if n > 1 or len(done) > 1 else "")
            + (" · en attente de validation" if ch.approval_required else "")
        )
        await db.log_event(f"{ch.name} : « {gs.title[:80]} » générée ({', '.join(done)})", "success", ch.id)
        video_ids = video_ids + long_ids

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
        progress: Progress, rng: random.Random, *, kind: str = VideoFormat.VERTICAL, reuse: dict | None = None,
    ) -> int:
        lang = get_language(ch.language)
        horizontal = kind == VideoFormat.HORIZONTAL
        W, H = self.settings.video_width, self.settings.video_height
        if horizontal:
            W, H = max(W, H), min(W, H)
        part_txt = f" · partie {index}/{count}" if count > 1 else ""
        async with db.session() as s:
            video = Video(
                channel_id=ch.id, story_id=story_id, part_index=index, part_count=count, format=kind,
                status=VideoStatus.RENDERING, title=title, text=text,
            )
            s.add(video)
            await s.flush()
            video_id = video.id

        workdir = self.settings.work_dir / f"video_{video_id}"
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            # 1) Voix (réutilisée pour la version longue : pas de seconde facture ElevenLabs)
            await progress.update("voice", "run", detail=part_txt.strip(" ·"))
            if reuse is not None and reuse.get("tts"):
                tts = reuse["tts"]
            else:
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
                if reuse is not None:
                    keep = self.settings.work_dir / f"voice_story_{story_id}"
                    keep.mkdir(parents=True, exist_ok=True)
                    audio = keep / tts.audio_path.name
                    shutil.copy2(tts.audio_path, audio)
                    reuse.update(dir=keep, tts=dataclasses.replace(tts, audio_path=audio, characters=0))
            duration = tts.duration + 0.5
            await progress.update("voice", "done", detail=f"{fmt_ts(tts.duration)} de narration{part_txt}")

            # 2) Fond : vidéo au hasard + séquence au hasard qui couvre toute la durée
            await progress.update("background", "run")
            bg_speed = min(4.0, max(0.25, ch.bg_speed or 1.0))
            seg = await self.bank.pick(  # à 1,5×, il faut 1,5 fois plus de vidéo source
                duration * bg_speed, categories=ch.bg_categories or None, recent=await self._recent_segments(), rng=rng,
                orientation="horizontal" if horizontal else "vertical",  # pas de recadrage quand c'est possible
            )
            rel = seg.path.relative_to(self.settings.backgrounds_dir) if seg.path.is_relative_to(self.settings.backgrounds_dir) else seg.path.name
            await progress.update(
                "background", "done",
                detail=f"{rel} · {fmt_ts(seg.start)} → {fmt_ts(seg.start + duration * bg_speed)} "
                       f"(sur {fmt_ts(seg.source_duration)})" + (f" · {bg_speed:g}×" if bg_speed != 1.0 else ""),
            )

            # 3) Sous-titres + carte d'accroche
            card_png, card_until = None, 0.0
            if ch.hook_card and index == 1 and hook:
                card_png = render_hook_card(hook, ch.name, workdir / "card.png", self.settings.fonts_dir,
                                            width=min(W, H))
                card_until = hook_end_time(tts.words, hook)
            label = f"{lang.part_word} {index}" if (ch.show_part_label and count > 1 and not horizontal) else None
            base_size = 92 if ch.caption_style == "trio" else 112
            style = CaptionStyle(
                mode=ch.caption_style, color=ch.caption_color, highlight=ch.highlight_color,
                position=ch.caption_position,
                size=int(base_size * min(W, H) / 1080 * (0.85 if horizontal else 1.0)),
            )
            ass = workdir / "subs.ass"
            ass.write_text(
                build_ass(tts.words, width=W, height=H,
                          style=style, label=label, duration=duration, captions_from=card_until),
                "utf-8",
            )

            # 4) Montage
            out_dir = self.settings.output_dir / f"{ch.id}-{slugify(ch.name, 24)}"
            suffix = "-long" if horizontal else (f"-p{index}" if count > 1 else "")
            out = out_dir / f"{video_id:05d}-{slugify(title)}{suffix}.mp4"
            throttle = Throttle()

            async def on_progress(frac: float) -> None:
                if throttle.ready():
                    await progress.update("render", "run", pct=frac, detail=part_txt.strip(" ·"))

            await progress.update("render", "run", pct=0.0, detail=part_txt.strip(" ·"))
            music = await self.music.pick(ch.music_tracks, duration, rng) if ch.music_volume > 0 else None
            await render(
                RenderJob(
                    segment=seg, narration=tts.audio_path, ass_file=ass, output=out, duration=duration,
                    card_png=card_png, card_until=card_until, music=music.path if music else None,
                    music_start=music.start if music else 0.0,
                    music_volume=ch.music_volume, bg_volume=ch.bg_volume, bg_speed=bg_speed, width=W, height=H,
                ),
                self.settings,
                on_progress=on_progress,
            )
            await progress.update("render", "done", detail=f"{W}×{H} · {fmt_ts(duration)}{part_txt}")

            # 5) Miniatures + aperçu Telegram
            await progress.update("finish", "run")
            thumb = await make_thumbnail(out, out.with_suffix(".jpg"), self.settings, at=min(2.0, duration / 3))
            cover = None
            if horizontal:
                # Image prise dans le fond brut (sans sous-titres) pour la miniature
                at = seg.start + min(30.0, duration / 4)
                if seg.source_duration:
                    at = min(at, max(0.0, seg.source_duration - 0.5))
                frame = await grab_frame(seg.path, workdir / "frame.jpg", self.settings, at=at)
                cover = render_cover(frame, title, out.with_name(out.stem + "_cover.jpg"), self.settings.fonts_dir)
            preview = await make_telegram_preview(out, out.with_name(out.stem + "_preview.mp4"), self.settings,
                                                  duration=duration)
            await progress.update("finish", "done")

            async with db.session() as s:
                v = await s.get(Video, video_id)
                v.file_path = str(out)
                v.thumb_path = str(thumb)
                v.preview_path = str(preview)
                v.cover_path = str(cover) if cover else None
                v.duration = duration
                v.file_size = out.stat().st_size
                v.bg_file = str(seg.path)
                v.bg_start = seg.start
                v.tts_chars = tts.characters
                v.words = [[w.text, round(w.start, 3), round(w.end, 3)] for w in tts.words]
            shutil.rmtree(workdir, ignore_errors=True)
            return video_id
        except Exception as exc:
            async with db.session() as s:
                v = await s.get(Video, video_id)
                if v:
                    v.status = VideoStatus.FAILED
                    v.error = str(exc)[:1000]
            raise

    async def _ai_event(self, text: str) -> None:
        await db.log_event(text, "warning")
        await self.notifier.notify(f"<b>IA des histoires</b>\n\n{html.escape(text)}", "warning")

    async def _tts_event(self, text: str) -> None:
        await db.log_event(text, "warning")
        await self.notifier.notify(f"<b>ElevenLabs</b>\n\n{html.escape(text)}", "warning")

    async def _get_video(self, video_id: int) -> Video | None:
        async with db.session() as s:
            return await s.get(Video, video_id)

    async def split_video(self, video_id: int, max_seconds: int, *, status: str | None = None) -> list[int]:
        """Découpe une vidéo en parties de max_seconds au plus (fins de phrase), label « PARTIE N ».

        Les parties remplacent la vidéo, qui est archivée (donc restaurable). Retourne les ids des parties.
        """
        async with db.session() as s:
            orig = await s.get(Video, video_id)
            if orig is None or not orig.file_path or not Path(orig.file_path).exists():
                raise ValueError("Vidéo introuvable ou fichier supprimé")
            if orig.part_count > 1:
                raise ValueError("Cette vidéo fait déjà partie d'une série")
            if orig.format == VideoFormat.HORIZONTAL:
                raise ValueError("Les vidéos longues ne se découpent pas")
            story = await s.get(Story, orig.story_id)
            ch = await s.get(Channel, orig.channel_id)
        segments = plan_cuts(orig.words, orig.duration, max_seconds)
        if len(segments) < 2:
            raise ValueError(f"La vidéo dure déjà moins de {max_seconds} s")
        lang = get_language(story.language if story else ch.language)
        src = Path(orig.file_path)
        dims = (await probe_dimensions(src, self.settings.ffprobe_bin)) or (self.settings.video_width, self.settings.video_height)
        workdir = self.settings.work_dir / f"split_{video_id}"
        new_status = status or orig.status
        n = len(segments)
        ids: list[int] = []
        try:
            for i, (a, b) in enumerate(segments, start=1):
                out = src.with_name(f"{src.stem}-p{i}.mp4")
                await cut_part(src, out, a, b, f"{lang.part_word} {i}", width=dims[0], height=dims[1],
                               workdir=workdir, settings=self.settings)
                thumb = await make_thumbnail(out, out.with_suffix(".jpg"), self.settings, at=min(2.0, (b - a) / 3))
                preview = await make_telegram_preview(out, out.with_name(out.stem + "_preview.mp4"), self.settings,
                                                      duration=b - a)
                part_words = words_between(orig.words, a, b)
                async with db.session() as s:
                    v = Video(
                        channel_id=orig.channel_id, story_id=orig.story_id, format=orig.format,
                        part_index=i, part_count=n, status=new_status, title=orig.title,
                        text=" ".join(w[0] for w in part_words) or orig.text,
                        file_path=str(out), thumb_path=str(thumb), preview_path=str(preview),
                        duration=round(b - a, 3), file_size=out.stat().st_size,
                        bg_file=orig.bg_file, bg_start=orig.bg_start + a, words=part_words,
                        note=f"Partie {i}/{n} de la vidéo #{video_id}",
                    )
                    s.add(v)
                    await s.flush()
                    ids.append(v.id)
        except Exception:
            async with db.session() as s:  # pas de série incomplète
                for vid in ids:
                    v = await s.get(Video, vid)
                    if v:
                        v.status = VideoStatus.FAILED
                        v.error = "Découpage interrompu"
            raise
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        async with db.session() as s:
            o = await s.get(Video, video_id)
            o.archived_from = o.status if o.status != VideoStatus.ARCHIVED else o.archived_from
            o.status = VideoStatus.ARCHIVED
            o.note = f"Découpée en {n} parties : " + ", ".join(f"#{i}" for i in ids)
        await db.log_event(f"{ch.name} : vidéo #{video_id} découpée en {n} parties", "info", ch.id)
        return ids

    # ------------------------------------------------------------------ publication
    async def publish(self, video_id: int, tiktok_mode: str | None = None) -> None:
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
            platforms = ch.platforms(video.format)
            if not platforms:
                await self.notifier.notify(
                    f"<b>Publication en attente</b> · {html.escape(ch.name)}\n\nAucune plateforme liée ou activée. "
                    f"La vidéo #{video_id} reste en stock.",
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
                mode=tiktok_mode or ch.tiktok_mode, kind=video.format, duration=video.duration or 0.0,
                tiktok=ch.tiktok_post,
                thumbnail_path=video.cover_path if video.cover_path and Path(video.cover_path).exists() else None,
            )
            try:
                if platform == "youtube":
                    result = await youtube.upload(ch.youtube_credentials or {}, req)
                    if result.credentials:
                        await self._save_creds(ch.id, "youtube_credentials", result.credentials)
                elif platform == "instagram":
                    creds, changed = await instagram.refresh_if_needed(ch.instagram_credentials or {})
                    if changed:
                        await self._save_creds(ch.id, "instagram_credentials", creds)
                    if not self.public_media_url:
                        raise PublisherError("Instagram a besoin de PUBLIC_URL (https) pour récupérer la vidéo")
                    req.video_url = self.public_media_url(video_id)
                    result = await instagram.publish(creds, req, version=self.settings.instagram_api_version)
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
                label = PLATFORM_LABELS.get(platform, platform)
                detail = msg if label.lower() in msg.lower() else f"{label} : {msg}"
                await db.log_event(f"{ch.name} · {detail}", "error", ch.id)

        async with db.session() as s:
            v = await s.get(Video, video_id)
            v.status = VideoStatus.PUBLISHED if any_ok else VideoStatus.FAILED
            if any_ok:
                v.published_at = utcnow()
                v.error = ""
            else:
                v.error = "Toutes les publications ont échoué"
        if any_ok:
            await db.log_event(f"{ch.name} : « {story.title[:70]} » publiée", "success", ch.id)
        await self.notifier.video_published(video_id)

    async def _save_creds(self, channel_id: int, field: str, creds: dict) -> None:
        async with db.session() as s:
            ch = await s.get(Channel, channel_id)
            if ch:
                setattr(ch, field, dict(creds))
