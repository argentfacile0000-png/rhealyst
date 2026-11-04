"""Conteneur de services partagé par le bot Telegram, la Mini App et le planificateur."""

from __future__ import annotations

import hashlib
import html
import hmac
import logging
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import delete, func, select, update

from . import db
from .config import Settings
from .jobs import Job, JobManager
from .models import Channel, Publication, PubStatus, Story, Video, VideoFormat, VideoStatus, utcnow
from .notify import NullNotifier, Notifier, Progress
from .pipeline import Pipeline
from .platform_apps import PlatformApps
from .publishers import PLATFORM_LABELS, PublisherError, instagram, tiktok, youtube
from .scheduler import Scheduler

log = logging.getLogger(__name__)

PAUSE_KEY = "system_paused"
STORY_PROMPT_KEY = "story_system_prompt"
PORTAL_TTL = 1800  # validité d'un lien de connexion des comptes (s)
MEDIA_SESSION_TTL = 2 * 3600  # validité du jeton de lecture des aperçus de la Mini App (s)


class OAuthResult(NamedTuple):
    platform: str
    channel: Channel
    account: str
    portal: str  # jeton du lien de connexion d'origine (vide si liaison depuis Telegram)


class OAuthStates:
    """États OAuth en attente (state -> plateforme, chaîne, PKCE), valables 30 min."""

    TTL = 1800

    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}

    def new(self, platform: str, channel_id: int, **extra: Any) -> str:
        self._gc()
        state = f"{platform[:2]}{channel_id}x{secrets.token_urlsafe(12)}"
        self._states[state] = {"platform": platform, "channel_id": channel_id, "ts": time.time(), **extra}
        return state

    def pop(self, state: str) -> dict[str, Any] | None:
        self._gc()
        return self._states.pop(state, None)

    def _gc(self) -> None:
        now = time.time()
        for k in [k for k, v in self._states.items() if now - v["ts"] > self.TTL]:
            self._states.pop(k, None)


class Services:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.notifier: Notifier = NullNotifier()
        self.pipeline = Pipeline(settings, self.notifier)
        self.pipeline.enqueue_publish = self.enqueue_publish
        self.pipeline.public_media_url = self.public_media_url
        self.jobs = JobManager(settings.max_concurrent_renders, 2, busy_file=settings.data_dir / "state" / "busy")
        self.scheduler = Scheduler(self)
        self.oauth = OAuthStates()
        self.apps = PlatformApps(settings)
        self.started_at = time.time()
        self._quota_cache: tuple[float, dict | None] = (0.0, None)
        self._failures: dict[str, tuple[int, float]] = {}  # "chaîne:format" -> (échecs consécutifs, reprise à)
        self.bot_username = ""  # renseigné au démarrage du bot (liens « Retour sur Telegram »)

    def set_notifier(self, notifier: Notifier) -> None:
        self.notifier = notifier
        self.pipeline.notifier = notifier

    # ------------------------------------------------------------------ cycle de vie
    async def start(self) -> None:
        self.settings.ensure_dirs()
        await db.init_db(self.settings.db_url)
        await self.apps.load()
        await self.pipeline.tts.keys.load()
        await self.pipeline.llm_keys.load()
        custom = await db.kv_get(STORY_PROMPT_KEY) or None
        if custom and custom.lstrip().startswith("VIRAL STORYTIME ENGINE — MASTER SYSTEM PROMPT"):
            # Ancienne version du prompt « Viral Storytime Engine » : la v2 (calquée sur les storytimes à succès)
            # est désormais le prompt d'origine.
            await db.kv_set(STORY_PROMPT_KEY, "")
            await db.log_event("Prompt des histoires : « Viral Storytime Engine v2 » appliqué")
            custom = None
        self.pipeline.stories.system_prompt = custom
        await self._recover()
        await self.jobs.start()
        await self.scheduler.start()
        log.info("Services démarrés")

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.jobs.stop()
        await db.close_db()

    async def _recover(self) -> None:
        """Après un redémarrage : les tâches interrompues sont marquées en échec."""
        async with db.session() as s:
            await s.execute(
                update(Video).where(Video.status == VideoStatus.RENDERING)
                .values(status=VideoStatus.FAILED, error="Interrompu par un redémarrage")
            )
            await s.execute(
                update(Video).where(Video.status == VideoStatus.PUBLISHING)
                .values(status=VideoStatus.FAILED, error="Publication interrompue par un redémarrage (vérifie la plateforme)")
            )
            await s.execute(
                update(Publication).where(Publication.status.in_([PubStatus.UPLOADING, PubStatus.PENDING]))
                .values(status=PubStatus.FAILED, error="Interrompu par un redémarrage")
            )

    # ------------------------------------------------------------------ pause globale
    async def is_paused(self) -> bool:
        return bool(await db.kv_get(PAUSE_KEY, False))

    async def set_story_prompt(self, text: str | None) -> None:
        """Prompt système des histoires : texte personnalisé, ou None pour revenir à l'original."""
        await db.kv_set(STORY_PROMPT_KEY, text or "")
        self.pipeline.stories.system_prompt = text or None
        await db.log_event("Prompt des histoires " + ("personnalisé" if text else "rétabli à l'original"))

    async def set_paused(self, paused: bool) -> None:
        await db.kv_set(PAUSE_KEY, paused)
        await db.log_event("Système mis en pause" if paused else "Système relancé", "warning" if paused else "info")

    # ------------------------------------------------------------------ files d'attente
    def missing_prereqs(self) -> list[str]:
        s = self.settings
        missing = []
        if not self.pipeline.llm_keys.all() and not (self.pipeline.stories._client or s.llm.api_key):
            missing.append("clé d'IA (Réglages, Clés API)")
        if not self.pipeline.tts.keys or not self.pipeline.tts.keys.has_keys():
            missing.append("clé ElevenLabs (Réglages, Clés API)")
        if not self.pipeline.bank.categories():
            missing.append("vidéos de fond")
        return missing

    def enqueue_generate(
        self, channel_id: int, *, kind: str = VideoFormat.VERTICAL, publish_after: bool = False,
        progress: Progress | None = None, label: str = "",
    ) -> Job:
        key = f"{channel_id}:{kind}"

        async def run() -> None:
            try:
                await self.pipeline.generate(channel_id, kind=kind, publish_after=publish_after, progress=progress)
                self._failures.pop(key, None)
            except Exception as exc:
                n = self._failures.get(key, (0, 0.0))[0] + 1
                delay = min(300 * 2 ** (n - 1), 7200)
                self._failures[key] = (n, time.time() + delay)
                if progress is None:  # génération automatique : on prévient quand même
                    ch = await self.get_channel(channel_id)
                    what = "Vidéo longue automatique échouée" if kind == VideoFormat.HORIZONTAL else "Génération automatique échouée"
                    await self.notifier.notify(
                        f"<b>{what}</b> · {html.escape(ch.name) if ch else channel_id}\n\n{html.escape(str(exc)[:300])}\n"
                        f"<i>Nouvel essai automatique dans {delay // 60} min.</i>",
                        "error", channel_id,
                    )
                raise

        prefix = "Vidéo longue" if kind == VideoFormat.HORIZONTAL else "Génération"
        return self.jobs.submit("generate", label or prefix, run, channel_id=channel_id, tag=kind)

    def enqueue_publish(self, video_id: int, channel_id: int | None = None, tiktok_mode: str | None = None) -> Job | None:
        if self.jobs.count(kind="publish", video_id=video_id):
            return None

        async def run() -> None:
            await self.pipeline.publish(video_id, tiktok_mode=tiktok_mode)

        return self.jobs.submit("publish", f"Publication #{video_id}", run, channel_id=channel_id, video_id=video_id)

    async def next_ready_video(self, channel_id: int, kind: str | None = VideoFormat.VERTICAL) -> Video | None:
        """Prochaine vidéo prête (dans l'ordre des parties) ; kind=None : tous formats."""
        async with db.session() as s:
            q = select(Video).where(Video.channel_id == channel_id, Video.status == VideoStatus.READY)
            if kind:
                q = q.where(Video.format == kind)
            return (
                await s.execute(q.order_by(Video.story_id, Video.part_index, Video.id).limit(1))
            ).scalar_one_or_none()

    async def publish_slot(self, channel_id: int, kind: str = VideoFormat.VERTICAL) -> None:
        """Créneau de publication : publie la prochaine vidéo prête du format (ou en génère une)."""
        if await self.is_paused():
            return
        async with db.session() as s:
            ch = await s.get(Channel, channel_id)
        if ch is None or not ch.active or kind not in ch.formats_enabled():
            return
        video = await self.next_ready_video(channel_id, kind)
        if video:
            self.enqueue_publish(video.id, channel_id)
            return
        label = "vidéo longue" if kind == VideoFormat.HORIZONTAL else "short"
        if not ch.approval_required:
            if not self.jobs.count(kind="generate", channel_id=channel_id, tag=kind):
                self.enqueue_generate(channel_id, kind=kind, publish_after=True, label=f"Créneau {label} · {ch.name}")
            else:
                await db.log_event(f"{ch.name} : créneau ({label}) atteint pendant une génération, publication à la fin",
                                   "warning", ch.id)
            return
        await self.notifier.notify(
            f"<b>Créneau manqué</b> · {html.escape(ch.name)}\n\nAucune {label} validée pour ce créneau.\n"
            "<i>Valide une vidéo en attente ou lance une génération.</i>",
            "warning", ch.id,
        )

    async def buffer_count(self, channel_id: int, kind: str = VideoFormat.VERTICAL) -> int:
        async with db.session() as s:
            n = (
                await s.execute(
                    select(func.count(Video.id)).where(
                        Video.channel_id == channel_id, Video.format == kind,
                        Video.status.in_(VideoStatus.IN_BUFFER),
                    )
                )
            ).scalar_one()
        return int(n) + self.jobs.count(kind="generate", channel_id=channel_id, tag=kind)

    async def refill(self) -> None:
        """Maintient une réserve de vidéos d'avance, par chaîne active et par format."""
        if await self.is_paused() or self.missing_prereqs():
            return
        async with db.session() as s:
            channels = (await s.execute(select(Channel).where(Channel.active.is_(True)))).scalars().all()
        for ch in channels:
            for kind in ch.formats_enabled():
                # La version longue naît avec chaque histoire des Shorts : pas de génération à part, sauf si les
                # Shorts sont désactivés.
                if kind == VideoFormat.HORIZONTAL and ch.shorts_enabled:
                    continue
                # Une génération à la fois par chaîne et par format (une série remplit plusieurs places).
                if self.jobs.count(kind="generate", channel_id=ch.id, tag=kind):
                    continue
                if self._failures.get(f"{ch.id}:{kind}", (0, 0.0))[1] > time.time():  # recul après échec
                    continue
                target = ch.long_buffer_size if kind == VideoFormat.HORIZONTAL else ch.buffer_size
                if await self.buffer_count(ch.id, kind) < max(1, target):
                    label = "Réserve vidéo longue" if kind == VideoFormat.HORIZONTAL else "Réserve"
                    self.enqueue_generate(ch.id, kind=kind, label=f"{label} · {ch.name}")

    async def cleanup_files(self) -> None:
        cutoff = utcnow() - timedelta(days=max(1, self.settings.keep_videos_days))
        removed = 0
        async with db.session() as s:
            videos = (
                await s.execute(
                    select(Video).where(
                        Video.created_at < cutoff,
                        Video.status.in_([VideoStatus.PUBLISHED, VideoStatus.DISCARDED, VideoStatus.FAILED]),
                        Video.file_path.is_not(None),
                    )
                )
            ).scalars().all()
            for v in videos:
                for p in {v.file_path, v.preview_path}:
                    if p and Path(p).exists():
                        Path(p).unlink(missing_ok=True)
                        removed += 1
                v.file_path = None
                v.preview_path = None
        if removed:
            await db.log_event(f"Nettoyage : {removed} fichier(s) vidéo supprimé(s)")

    # ------------------------------------------------------------------ chaînes
    async def create_channel(self, name: str, **fields: Any) -> Channel:
        async with db.session() as s:
            ch = Channel(
                name=name.strip()[:80],
                voice_id=self.settings.elevenlabs_default_voice_id,
                voice_name=self.settings.elevenlabs_default_voice_name,
                timezone=self.settings.timezone,
                **fields,
            )
            s.add(ch)
            await s.flush()
        await db.log_event(f"Nouvelle chaîne : {ch.name}", "info", ch.id)
        return ch

    async def get_channel(self, channel_id: int) -> Channel | None:
        async with db.session() as s:
            return await s.get(Channel, channel_id)

    async def list_channels(self) -> list[Channel]:
        async with db.session() as s:
            return list((await s.execute(select(Channel).order_by(Channel.id))).scalars().all())

    async def update_channel(self, channel_id: int, **fields: Any) -> Channel:
        async with db.session() as s:
            ch = await s.get(Channel, channel_id)
            if ch is None:
                raise ValueError("Chaîne introuvable")
            for k, v in fields.items():
                if not hasattr(Channel, k):
                    raise AttributeError(k)
                setattr(ch, k, v)
        self.scheduler.sync_channel(ch)
        return ch

    async def delete_channel(self, channel_id: int) -> None:
        async with db.session() as s:
            await s.execute(delete(Publication).where(Publication.channel_id == channel_id))
            await s.execute(delete(Video).where(Video.channel_id == channel_id))
            await s.execute(delete(Story).where(Story.channel_id == channel_id))
            await s.execute(delete(Channel).where(Channel.id == channel_id))
        self.scheduler.remove_channel(channel_id)
        await db.log_event(f"Chaîne #{channel_id} supprimée", "warning")

    # ------------------------------------------------------------------ vidéos
    async def get_video(self, video_id: int) -> Video | None:
        async with db.session() as s:
            return await s.get(Video, video_id)

    async def set_video_status(self, video_id: int, status: str) -> Video | None:
        async with db.session() as s:
            v = await s.get(Video, video_id)
            if v:
                v.status = status
            return v

    async def approve(self, video_id: int) -> Video | None:
        v = await self.set_video_status(video_id, VideoStatus.READY)
        if v:
            await db.log_event(f"Vidéo #{video_id} validée", "info", v.channel_id)
        return v

    async def discard(self, video_id: int) -> Video | None:
        v = await self.set_video_status(video_id, VideoStatus.DISCARDED)
        if v:
            for p in (v.file_path, v.preview_path):
                if p and p != v.thumb_path:
                    Path(p).unlink(missing_ok=True)
            async with db.session() as s:
                vv = await s.get(Video, video_id)
                vv.file_path = None
                vv.preview_path = None
            await db.log_event(f"Vidéo #{video_id} jetée", "info", v.channel_id)
        return v

    # ------------------------------------------------------------------ archives, programmation, découpage
    async def archive(self, video_id: int) -> Video:
        async with db.session() as s:
            v = await s.get(Video, video_id)
            if v is None:
                raise ValueError("Vidéo introuvable")
            if v.status not in VideoStatus.ARCHIVABLE:
                raise ValueError("Cette vidéo ne peut pas être archivée dans son état actuel")
            if v.status == VideoStatus.SCHEDULED:
                self.scheduler.unschedule_video(video_id)
            v.archived_from = v.status
            v.status = VideoStatus.ARCHIVED
            v.scheduled_at = None
        await db.log_event(f"Vidéo #{video_id} archivée", "info", v.channel_id)
        return v

    async def unarchive(self, video_id: int) -> Video:
        async with db.session() as s:
            v = await s.get(Video, video_id)
            if v is None or v.status != VideoStatus.ARCHIVED:
                raise ValueError("Cette vidéo n'est pas archivée")
            ch = await s.get(Channel, v.channel_id)
            restore = v.archived_from
            if restore not in (VideoStatus.PENDING_APPROVAL, VideoStatus.READY, VideoStatus.FAILED):
                restore = VideoStatus.PENDING_APPROVAL if ch and ch.approval_required else VideoStatus.READY
            if not v.file_path or not Path(v.file_path).exists():
                restore, v.error = VideoStatus.FAILED, "Fichier vidéo supprimé"
            v.status = restore
            v.archived_from = ""
        await db.log_event(f"Vidéo #{video_id} désarchivée", "info", v.channel_id)
        return v

    async def schedule(self, video_id: int, when: datetime) -> Video:
        """Programme la publication d'une vidéo à une date précise (when : date avec fuseau)."""
        when_utc = when.astimezone(timezone.utc).replace(tzinfo=None)
        async with db.session() as s:
            v = await s.get(Video, video_id)
            if v is None:
                raise ValueError("Vidéo introuvable")
            if v.status not in VideoStatus.SCHEDULABLE or not v.file_path:
                raise ValueError("Cette vidéo ne peut pas être programmée")
            v.status = VideoStatus.SCHEDULED
            v.scheduled_at = when_utc
            v.archived_from = ""
        self.scheduler.schedule_video(video_id, when_utc)
        await db.log_event(f"Vidéo #{video_id} programmée", "info", v.channel_id)
        return v

    async def unschedule(self, video_id: int) -> Video:
        self.scheduler.unschedule_video(video_id)
        async with db.session() as s:
            v = await s.get(Video, video_id)
            if v is None or v.status != VideoStatus.SCHEDULED:
                raise ValueError("Cette vidéo n'est pas programmée")
            v.status = VideoStatus.READY
            v.scheduled_at = None
        await db.log_event(f"Programmation de la vidéo #{video_id} annulée", "info", v.channel_id)
        return v

    async def publish_scheduled(self, video_id: int) -> None:
        v = await self.get_video(video_id)
        if v is None or v.status != VideoStatus.SCHEDULED:
            return
        if await self.is_paused():  # pause globale : on repousse de 15 min
            retry = datetime.now(timezone.utc) + timedelta(minutes=15)
            async with db.session() as s:
                vv = await s.get(Video, video_id)
                vv.scheduled_at = retry.replace(tzinfo=None)
            self.scheduler.schedule_video(video_id, retry)
            await db.log_event(f"Vidéo #{video_id} : publication programmée repoussée (système en pause)", "warning",
                               v.channel_id)
            return
        await self.set_video_status(video_id, VideoStatus.READY)
        self.enqueue_publish(video_id, v.channel_id)

    async def split(self, video_id: int, max_seconds: int) -> list[int]:
        """Découpe à la demande ; les parties suivent le même parcours (validation ou stock)."""
        v = await self.get_video(video_id)
        if v is None:
            raise ValueError("Vidéo introuvable")
        if v.status not in (VideoStatus.PENDING_APPROVAL, VideoStatus.READY, VideoStatus.ARCHIVED, VideoStatus.FAILED):
            raise ValueError("Cette vidéo ne peut pas être découpée dans son état actuel")
        status = v.status
        if status in (VideoStatus.ARCHIVED, VideoStatus.FAILED):
            ch = await self.get_channel(v.channel_id)
            status = VideoStatus.PENDING_APPROVAL if ch and ch.approval_required else VideoStatus.READY
        ids = await self.pipeline.split_video(video_id, max_seconds, status=status)
        if hasattr(self.notifier, "refresh_video_messages"):
            await self.notifier.refresh_video_messages(video_id)
        if status == VideoStatus.PENDING_APPROVAL:
            for vid in ids:
                await self.notifier.video_for_approval(vid)
        return ids

    # ------------------------------------------------------------------ comptes & OAuth
    def portal_available(self) -> bool:
        """La page de connexion n'est joignable que si le serveur web tourne derrière une URL https."""
        s = self.settings
        return bool(s.webapp_enabled and s.public_base.startswith("https://"))

    def _portal_key(self) -> bytes:
        s = self.settings
        base = s.secret_key or s.telegram_bot_token or "storyforge"
        return hashlib.sha256(f"portal:{base}".encode()).digest()

    def _portal_sig(self, payload: str) -> str:
        return hmac.new(self._portal_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]

    def portal_token(self, channel_id: int, ttl: int = PORTAL_TTL) -> str:
        payload = f"{channel_id}.{int(time.time()) + ttl}"
        return f"{payload}.{self._portal_sig(payload)}"

    def verify_portal_token(self, token: str) -> tuple[int, int] | None:
        """Retourne (chaîne, expiration) si le lien est authentique et encore valable."""
        try:
            cid, exp, sig = token.split(".")
            if not hmac.compare_digest(sig, self._portal_sig(f"{cid}.{exp}")) or int(exp) < time.time():
                return None
            return int(cid), int(exp)
        except ValueError:
            return None

    def portal_link(self, channel_id: int) -> str:
        return f"{self.settings.public_base}/connect/{self.portal_token(channel_id)}"

    def google_client_file(self, channel_id: int) -> Path:
        """Client OAuth propre à la chaîne s'il existe (répartit le quota YouTube), sinon le global."""
        default = self.settings.google_client_secrets_file
        specific = default.with_name(f"{default.stem}_{channel_id}{default.suffix}")
        return specific if specific.exists() else default

    def youtube_redirect_uri(self, channel_id: int) -> str:
        # Un client Google de type « Application Web » peut revenir directement sur notre serveur.
        if self.portal_available() and youtube.client_type(self.google_client_file(channel_id)) == "web":
            return f"{self.settings.public_base}/oauth/youtube"
        return self.settings.google_redirect_uri

    def tiktok_redirect_uri(self) -> str:
        s = self.settings
        if s.tiktok_redirect_uri:
            return s.tiktok_redirect_uri
        return f"{s.public_base}/oauth/tiktok" if self.portal_available() else ""

    def instagram_redirect_uri(self) -> str:
        s = self.settings
        if s.instagram_redirect_uri:
            return s.instagram_redirect_uri
        return f"{s.public_base}/oauth/instagram" if self.portal_available() else ""

    def redirect_uri(self, platform: str, channel_id: int) -> str:
        if platform == "youtube":
            return self.youtube_redirect_uri(channel_id)
        if platform == "instagram":
            return self.instagram_redirect_uri()
        return self.tiktok_redirect_uri()

    def oauth_is_automatic(self, platform: str, channel_id: int) -> bool:
        """Vrai si la plateforme renvoie directement vers notre serveur (sinon : coller l'adresse)."""
        redirect = self.redirect_uri(platform, channel_id)
        return self.portal_available() and redirect.startswith(f"{self.settings.public_base}/oauth/")

    # Adresse publique signée d'une vidéo (Instagram télécharge les Reels lui-même)
    def _media_sig(self, video_id: int, exp: int) -> str:
        return hmac.new(self._portal_key(), f"media:{video_id}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]

    def public_media_url(self, video_id: int, ttl: int = 6 * 3600) -> str:
        if not self.portal_available():
            raise PublisherError("Instagram a besoin d'une adresse publique en https (PUBLIC_URL et WEBAPP_ENABLED)")
        exp = int(time.time()) + ttl
        return f"{self.settings.public_base}/media/public/{video_id}/{exp}/{self._media_sig(video_id, exp)}.mp4"

    def verify_media(self, video_id: int, exp: int, sig: str) -> bool:
        return exp >= time.time() and hmac.compare_digest(sig, self._media_sig(video_id, exp))

    # Jeton de lecture des aperçus pour la Mini App : n'autorise que l'affichage des vidéos et miniatures,
    # jamais une action. Évite de faire circuler la session Telegram dans les adresses des médias.
    def _session_sig(self, payload: str) -> str:
        return hmac.new(self._portal_key(), f"mediasession:{payload}".encode(), hashlib.sha256).hexdigest()[:32]

    def media_session_token(self, user_id: int, ttl: int = MEDIA_SESSION_TTL) -> str:
        # Expiration arrondie à l'heure : le jeton (et donc l'adresse des miniatures) reste stable entre
        # deux rafraîchissements, le navigateur garde les images en cache.
        exp = (int(time.time()) + ttl) // 3600 * 3600 + 3600
        payload = f"{int(user_id)}.{exp}"
        return f"{payload}.{self._session_sig(payload)}"

    def verify_media_session(self, token: str) -> int | None:
        """Identifiant de l'administrateur si le jeton est authentique et valable, sinon None."""
        try:
            uid, exp, sig = token.split(".")
            if not hmac.compare_digest(sig, self._session_sig(f"{uid}.{exp}")) or int(exp) < time.time():
                return None
            user_id = int(uid)
        except ValueError:
            return None
        return user_id if user_id in self.settings.admin_ids else None

    def platform_problem(self, platform: str, channel_id: int) -> str | None:
        """Message expliquant ce qui manque pour connecter cette plateforme, ou None."""
        s = self.settings
        if platform == "youtube":
            if not self.google_client_file(channel_id).exists():
                return "Il manque le client OAuth Google (fichier JSON de ton app Google Cloud)."
            return None
        if platform == "instagram":
            if not (s.instagram_app_id and s.instagram_app_secret):
                return "Il manque l'ID et la clé secrète de ton app Instagram (Meta for Developers)."
            if not self.portal_available():
                return "Instagram télécharge les vidéos sur ton serveur : il faut une adresse publique en https (PUBLIC_URL)."
            return None
        if not (s.tiktok_client_key and s.tiktok_client_secret):
            return "Il manque le Client key et le Client secret de ton app TikTok (TikTok for Developers)."
        if not self.tiktok_redirect_uri():
            return "Il manque l'adresse de retour TikTok : configure l'adresse HTTPS du serveur (PUBLIC_URL)."
        return None

    def youtube_auth_url(self, channel_id: int, portal: str = "") -> str:
        redirect = self.youtube_redirect_uri(channel_id)
        secrets_file = self.google_client_file(channel_id)
        state = self.oauth.new("youtube", channel_id, redirect=redirect, secrets=str(secrets_file), portal=portal)
        url, verifier = youtube.build_auth_url(secrets_file, redirect, state)
        self.oauth._states[state]["verifier"] = verifier
        return url

    def tiktok_auth_url(self, channel_id: int, portal: str = "") -> str:
        redirect = self.tiktok_redirect_uri()
        state = self.oauth.new("tiktok", channel_id, redirect=redirect, portal=portal)
        return tiktok.build_auth_url(self.settings.tiktok_client_key, redirect, state, direct=self.apps.tiktok_direct)

    def instagram_auth_url(self, channel_id: int, portal: str = "") -> str:
        redirect = self.instagram_redirect_uri()
        state = self.oauth.new("instagram", channel_id, redirect=redirect, portal=portal)
        return instagram.build_auth_url(self.settings.instagram_app_id, redirect, state)

    def auth_url(self, platform: str, channel_id: int, portal: str = "") -> str:
        if platform == "youtube":
            return self.youtube_auth_url(channel_id, portal=portal)
        if platform == "instagram":
            return self.instagram_auth_url(channel_id, portal=portal)
        return self.tiktok_auth_url(channel_id, portal=portal)

    async def complete_oauth(self, state: str, code: str, platform: str | None = None) -> OAuthResult:
        info = self.oauth.pop(state) if state else None
        if not info:
            raise PublisherError("Lien d'autorisation expiré ou inconnu : relance la connexion")
        if platform and info["platform"] != platform:
            raise PublisherError("Cette réponse ne correspond pas à la plateforme demandée : relance la connexion")
        if not code:
            raise PublisherError("Réponse d'autorisation incomplète (code manquant) : relance la connexion")
        platform, channel_id = info["platform"], info["channel_id"]
        s = self.settings
        if platform == "youtube":
            secrets_file = Path(info.get("secrets") or s.google_client_secrets_file)
            creds = await youtube.exchange_code(secrets_file, info["redirect"], code, info.get("verifier"))
            try:
                account, creds = await youtube.channel_title(creds)
            except Exception as exc:
                log.warning("Titre de chaîne YouTube indisponible: %s", exc)
                account = "compte Google"
            ch = await self.update_channel(channel_id, youtube_credentials=creds, youtube_account=account)
        elif platform == "instagram":
            version = s.instagram_api_version
            creds = await instagram.exchange_code(s.instagram_app_id, s.instagram_app_secret, info["redirect"], code,
                                                  version=version)
            try:
                me = await instagram.user_info(creds["access_token"], version=version)
                account = f"@{me['username']}" if me.get("username") else "compte Instagram"
                creds["user_id"] = str(me.get("user_id") or creds.get("user_id", ""))
            except Exception as exc:
                log.warning("Infos Instagram indisponibles: %s", exc)
                account = "compte Instagram"
            ch = await self.update_channel(channel_id, instagram_credentials=creds, instagram_account=account)
        else:
            creds = await tiktok.exchange_code(s.tiktok_client_key, s.tiktok_client_secret, info["redirect"], code)
            try:
                user = await tiktok.user_info(creds["access_token"])
                account = user.get("display_name") or "compte TikTok"
            except Exception as exc:
                log.warning("Infos TikTok indisponibles: %s", exc)
                account = "compte TikTok"
            ch = await self.update_channel(channel_id, tiktok_credentials=creds, tiktok_account=account)
        await db.log_event(f"{ch.name} : {PLATFORM_LABELS[platform]} connecté ({account})", "success", channel_id)
        return OAuthResult(platform, ch, account, info.get("portal", ""))

    async def complete_oauth_from_url(self, pasted: str) -> OAuthResult:
        """L'utilisateur colle l'adresse de retour (contenant ?code=...&state=...)."""
        parsed = urllib.parse.urlparse(pasted.strip())
        q = urllib.parse.parse_qs(parsed.query)
        if "error" in q:
            raise PublisherError(f"Autorisation refusée : {q['error'][0]}")
        code = (q.get("code") or [""])[0]
        state = (q.get("state") or [""])[0]
        if not code or not state:
            raise PublisherError("Adresse invalide : elle doit contenir code= et state=")
        return await self.complete_oauth(state, code)

    # ------------------------------------------------------------------ TikTok : réglages de publication
    async def tiktok_creator(self, channel_id: int) -> dict:
        """Infos du créateur TikTok (rafraîchit le jeton si besoin)."""
        ch = await self.get_channel(channel_id)
        if ch is None or not ch.tiktok_linked:
            raise PublisherError("Compte TikTok non connecté")
        s = self.settings
        creds, changed = await tiktok.refresh_if_needed(s.tiktok_client_key, s.tiktok_client_secret,
                                                        ch.tiktok_credentials or {})
        if changed:
            await self.update_channel(channel_id, tiktok_credentials=creds)
        return await tiktok.fetch_creator_info(creds["access_token"])

    async def save_tiktok_post(self, channel_id: int, settings: dict, creator: dict, *, auto: bool) -> list[str]:
        """Enregistre les réglages choisis sur l'écran TikTok. Retourne les erreurs (rien n'est enregistré alors)."""
        errors = tiktok.check_post_settings(settings, creator)
        if errors:
            return errors
        clean = {k: bool(settings.get(k)) for k in ("comment", "duet", "stitch", "disclose", "brand_organic",
                                                     "brand_content")}
        clean.update(privacy=settings["privacy"], consented_at=utcnow().isoformat(timespec="seconds"),
                     nickname=str(creator.get("creator_nickname") or ""))
        ch = await self.update_channel(channel_id, tiktok_post=clean, tiktok_mode="direct" if auto else "draft")
        mode = "publication automatique" if auto else "envoi en brouillon"
        await db.log_event(f"{ch.name} : réglages de publication TikTok enregistrés ({mode}, "
                           f"{tiktok.PRIVACY_LABELS.get(clean['privacy'], clean['privacy'])})", "success", channel_id)
        return []

    async def unlink(self, channel_id: int, platform: str) -> Channel:
        if platform not in PLATFORM_LABELS:
            raise ValueError(platform)
        ch = await self.update_channel(channel_id, **{f"{platform}_credentials": None, f"{platform}_account": ""})
        await db.log_event(f"{ch.name} : {PLATFORM_LABELS[platform]} déconnecté", "info", channel_id)
        return ch

    # ------------------------------------------------------------------ quotas
    async def elevenlabs_quota(self, refresh: bool = False) -> dict | None:
        """Crédits additionnés sur toutes les clés + détail par clé (cache 10 min)."""
        ts, data = self._quota_cache
        if not refresh and time.time() - ts < 600:
            return data
        ring = self.pipeline.tts.keys
        details = []
        for k in ring.all() if ring else []:
            entry = {"kid": k.kid, "masked": k.masked, "source": k.source, "status": ring.status(k)}
            try:
                sub = await self.pipeline.tts.subscription(api_key=k.key)
                entry.update(used=int(sub.get("character_count", 0)), limit=int(sub.get("character_limit", 0)),
                             tier=sub.get("tier", ""), reset=sub.get("next_character_count_reset_unix"))
            except Exception as exc:
                entry.update(error=str(exc)[:120])
            details.append(entry)
        ok = [d for d in details if "limit" in d]
        if not ok:
            data = {"used": 0, "limit": 0, "tier": "", "reset": None, "keys": details} if details else None
        else:
            data = {
                "used": sum(d["used"] for d in ok),
                "limit": sum(d["limit"] for d in ok),
                "tier": ok[0]["tier"] if len(ok) == 1 else f"{len(ok)} clés",
                "reset": min((d["reset"] for d in ok if d.get("reset")), default=None),
                "keys": details,
            }
        self._quota_cache = (time.time(), data)
        return data
