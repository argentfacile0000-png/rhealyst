"""Conteneur de services partagé par le bot Telegram, la Mini App et le planificateur."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import urllib.parse
from datetime import timedelta
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import delete, func, select, update

from . import db
from .config import Settings
from .jobs import Job, JobManager
from .models import Channel, Publication, PubStatus, Story, Video, VideoStatus, utcnow
from .notify import NullNotifier, Notifier, Progress
from .pipeline import Pipeline
from .publishers import PublisherError, tiktok, youtube
from .scheduler import Scheduler

log = logging.getLogger(__name__)

PAUSE_KEY = "system_paused"
PORTAL_TTL = 1800  # validité d'un lien de connexion des comptes (s)


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
        self.jobs = JobManager(settings.max_concurrent_renders, 2)
        self.scheduler = Scheduler(self)
        self.oauth = OAuthStates()
        self.started_at = time.time()
        self._quota_cache: tuple[float, dict | None] = (0.0, None)
        self._failures: dict[int, tuple[int, float]] = {}  # chaîne -> (échecs consécutifs, reprise à)
        self.bot_username = ""  # renseigné au démarrage du bot (liens « Retour sur Telegram »)

    def set_notifier(self, notifier: Notifier) -> None:
        self.notifier = notifier
        self.pipeline.notifier = notifier

    # ------------------------------------------------------------------ cycle de vie
    async def start(self) -> None:
        self.settings.ensure_dirs()
        await db.init_db(self.settings.db_url)
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

    async def set_paused(self, paused: bool) -> None:
        await db.kv_set(PAUSE_KEY, paused)
        await db.log_event("Système mis en pause" if paused else "Système relancé", "warning" if paused else "info")

    # ------------------------------------------------------------------ files d'attente
    def missing_prereqs(self) -> list[str]:
        s = self.settings
        missing = []
        if not s.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not s.elevenlabs_api_key:
            missing.append("ELEVENLABS_API_KEY")
        if not self.pipeline.bank.categories():
            missing.append("vidéos de fond")
        return missing

    def enqueue_generate(
        self, channel_id: int, *, publish_after: bool = False, progress: Progress | None = None, label: str = ""
    ) -> Job:
        async def run() -> None:
            try:
                await self.pipeline.generate(channel_id, publish_after=publish_after, progress=progress)
                self._failures.pop(channel_id, None)
            except Exception as exc:
                n = self._failures.get(channel_id, (0, 0.0))[0] + 1
                delay = min(300 * 2 ** (n - 1), 7200)
                self._failures[channel_id] = (n, time.time() + delay)
                if progress is None:  # génération automatique : on prévient quand même
                    ch = await self.get_channel(channel_id)
                    await self.notifier.notify(
                        f"<b>Génération automatique échouée</b> · {ch.name if ch else channel_id}\n\n{str(exc)[:300]}\n"
                        f"<i>Nouvel essai automatique dans {delay // 60} min.</i>",
                        "error", channel_id,
                    )
                raise

        return self.jobs.submit("generate", label or "Génération", run, channel_id=channel_id)

    def enqueue_publish(self, video_id: int, channel_id: int | None = None) -> Job | None:
        if self.jobs.count(kind="publish", video_id=video_id):
            return None

        async def run() -> None:
            await self.pipeline.publish(video_id)

        return self.jobs.submit("publish", f"Publication #{video_id}", run, channel_id=channel_id, video_id=video_id)

    async def next_ready_video(self, channel_id: int) -> Video | None:
        async with db.session() as s:
            return (
                await s.execute(
                    select(Video)
                    .where(Video.channel_id == channel_id, Video.status == VideoStatus.READY)
                    .order_by(Video.story_id, Video.part_index, Video.id)
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def publish_slot(self, channel_id: int) -> None:
        """Créneau de publication : publie la prochaine vidéo prête (ou en génère une)."""
        if await self.is_paused():
            return
        async with db.session() as s:
            ch = await s.get(Channel, channel_id)
        if ch is None or not ch.active:
            return
        video = await self.next_ready_video(channel_id)
        if video:
            self.enqueue_publish(video.id, channel_id)
            return
        if not ch.approval_required:
            if not self.jobs.count(kind="generate", channel_id=channel_id):
                self.enqueue_generate(channel_id, publish_after=True, label=f"Créneau · {ch.name}")
            else:
                await db.log_event(f"{ch.name} : créneau atteint pendant une génération, publication à la fin", "warning", ch.id)
            return
        await self.notifier.notify(
            f"<b>Créneau manqué</b> · {ch.name}\n\nAucune vidéo validée pour ce créneau.\n"
            "<i>Valide une vidéo en attente ou lance une génération.</i>",
            "warning", ch.id,
        )

    async def buffer_count(self, channel_id: int) -> int:
        async with db.session() as s:
            n = (
                await s.execute(
                    select(func.count(Video.id)).where(
                        Video.channel_id == channel_id, Video.status.in_(VideoStatus.IN_BUFFER)
                    )
                )
            ).scalar_one()
        return int(n) + self.jobs.count(kind="generate", channel_id=channel_id)

    async def refill(self) -> None:
        """Maintient `buffer_size` vidéos d'avance pour chaque chaîne active."""
        if await self.is_paused() or self.missing_prereqs():
            return
        async with db.session() as s:
            channels = (await s.execute(select(Channel).where(Channel.active.is_(True)))).scalars().all()
        for ch in channels:
            # Une génération à la fois par chaîne : une histoire multi-parties remplit plusieurs places.
            if self.jobs.count(kind="generate", channel_id=ch.id):
                continue
            if self._failures.get(ch.id, (0, 0.0))[1] > time.time():  # recul après échec
                continue
            if await self.buffer_count(ch.id) < max(1, ch.buffer_size):
                self.enqueue_generate(ch.id, label=f"Réserve · {ch.name}")

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

    def oauth_is_automatic(self, platform: str, channel_id: int) -> bool:
        """Vrai si la plateforme renvoie directement vers notre serveur (sinon : coller l'adresse)."""
        redirect = self.youtube_redirect_uri(channel_id) if platform == "youtube" else self.tiktok_redirect_uri()
        return self.portal_available() and redirect.startswith(f"{self.settings.public_base}/oauth/")

    def platform_problem(self, platform: str, channel_id: int) -> str | None:
        """Message expliquant ce qui manque pour connecter cette plateforme, ou None."""
        s = self.settings
        if platform == "youtube":
            if not self.google_client_file(channel_id).exists():
                return f"Le fichier OAuth Google {s.google_client_secrets_file} est introuvable sur le serveur."
            return None
        if not (s.tiktok_client_key and s.tiktok_client_secret):
            return "TIKTOK_CLIENT_KEY et TIKTOK_CLIENT_SECRET ne sont pas renseignés dans le .env."
        if not self.tiktok_redirect_uri():
            return "TIKTOK_REDIRECT_URI n'est pas renseigné dans le .env."
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
        return tiktok.build_auth_url(self.settings.tiktok_client_key, redirect, state)

    async def complete_oauth(self, state: str, code: str) -> OAuthResult:
        info = self.oauth.pop(state)
        if not info:
            raise PublisherError("Lien d'autorisation expiré ou inconnu : relance la connexion")
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
        else:
            creds = await tiktok.exchange_code(s.tiktok_client_key, s.tiktok_client_secret, info["redirect"], code)
            try:
                user = await tiktok.user_info(creds["access_token"])
                account = user.get("display_name") or "compte TikTok"
            except Exception as exc:
                log.warning("Infos TikTok indisponibles: %s", exc)
                account = "compte TikTok"
            ch = await self.update_channel(channel_id, tiktok_credentials=creds, tiktok_account=account)
        label = "YouTube" if platform == "youtube" else "TikTok"
        await db.log_event(f"{ch.name} : {label} connecté ({account})", "success", channel_id)
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

    async def unlink(self, channel_id: int, platform: str) -> Channel:
        fields = ({"youtube_credentials": None, "youtube_account": ""} if platform == "youtube"
                  else {"tiktok_credentials": None, "tiktok_account": ""})
        ch = await self.update_channel(channel_id, **fields)
        await db.log_event(f"{ch.name} : {'YouTube' if platform == 'youtube' else 'TikTok'} déconnecté", "info", channel_id)
        return ch

    # ------------------------------------------------------------------ quotas
    async def elevenlabs_quota(self) -> dict | None:
        ts, data = self._quota_cache
        if time.time() - ts < 600:
            return data
        try:
            sub = await self.pipeline.tts.subscription()
            data = {
                "used": int(sub.get("character_count", 0)),
                "limit": int(sub.get("character_limit", 0)),
                "tier": sub.get("tier", ""),
                "reset": sub.get("next_character_count_reset_unix"),
            }
        except Exception as exc:
            log.info("Quota ElevenLabs indisponible: %s", exc)
            data = None
        self._quota_cache = (time.time(), data)
        return data
