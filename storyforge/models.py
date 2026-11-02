"""Modèles de données (SQLAlchemy 2.x)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class VideoStatus:
    RENDERING = "rendering"
    PENDING_APPROVAL = "pending_approval"
    READY = "ready"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    DISCARDED = "discarded"
    ARCHIVED = "archived"  # mise de côté, sans décision (restaurable)
    SCHEDULED = "scheduled"  # publication programmée à une date précise

    # Vidéos "dans le tuyau" (comptent dans le buffer d'une chaîne).
    IN_BUFFER = (RENDERING, PENDING_APPROVAL, READY)
    # Statuts qu'on peut archiver / programmer
    ARCHIVABLE = (PENDING_APPROVAL, READY, FAILED, SCHEDULED)
    SCHEDULABLE = (PENDING_APPROVAL, READY, FAILED, ARCHIVED, SCHEDULED)


class VideoFormat:
    VERTICAL = "vertical"  # Shorts / TikTok / Reels (9:16)
    HORIZONTAL = "horizontal"  # vidéo longue YouTube (16:9)


class PubStatus:
    PENDING = "pending"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    PUBLISHED = "published"
    DRAFT = "draft"  # TikTok : envoyée dans la boîte de réception de l'app
    FAILED = "failed"

    OK = (PUBLISHED, DRAFT)


class Channel(Base):
    """Une instance = une "chaîne" avec sa ligne éditoriale et ses comptes YouTube/TikTok."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # --- Histoire ----------------------------------------------------------
    language: Mapped[str] = mapped_column(String(8), default="fr")
    formats: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["askreddit", "drama", "revenge"])
    narrator_gender: Mapped[str] = mapped_column(String(10), default="any")  # any | female | male
    custom_instructions: Mapped[str] = mapped_column(Text, default="")

    # --- Voix (ElevenLabs) -------------------------------------------------
    voice_id: Mapped[str] = mapped_column(String(64), default="")
    voice_name: Mapped[str] = mapped_column(String(80), default="")
    voice_speed: Mapped[float] = mapped_column(Float, default=1.1)  # vitesse ElevenLabs (0.7-1.2)
    speedup: Mapped[float] = mapped_column(Float, default=1.2)  # accélération ffmpeg post-TTS
    voice_stability: Mapped[float] = mapped_column(Float, default=0.45)
    voice_similarity: Mapped[float] = mapped_column(Float, default=0.8)
    voice_style: Mapped[float] = mapped_column(Float, default=0.2)

    # --- Vidéo -------------------------------------------------------------
    bg_categories: Mapped[list[str]] = mapped_column(JSON, default=list)  # vide = toutes
    min_seconds: Mapped[int] = mapped_column(Integer, default=65)
    max_seconds: Mapped[int] = mapped_column(Integer, default=95)
    parts: Mapped[int] = mapped_column(Integer, default=1)
    split_seconds: Mapped[int] = mapped_column(Integer, default=0)  # découpage auto des Shorts (0 = désactivé)
    caption_style: Mapped[str] = mapped_column(String(16), default="pop")  # pop | trio
    caption_color: Mapped[str] = mapped_column(String(9), default="#FFFFFF")
    highlight_color: Mapped[str] = mapped_column(String(9), default="#FFE14D")
    caption_position: Mapped[int] = mapped_column(Integer, default=50)  # % de la hauteur
    show_part_label: Mapped[bool] = mapped_column(Boolean, default=True)
    hook_card: Mapped[bool] = mapped_column(Boolean, default=False)
    music_volume: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = pas de bande son
    music_tracks: Mapped[list[str] | None] = mapped_column(JSON, default=list)  # vide = toute la banque
    bg_volume: Mapped[float] = mapped_column(Float, default=0.0)

    # --- Formats -----------------------------------------------------------
    shorts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # vidéos verticales
    long_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # vidéos longues YouTube (16:9)
    long_min_minutes: Mapped[int] = mapped_column(Integer, default=8)
    long_max_minutes: Mapped[int] = mapped_column(Integer, default=12)
    long_schedule_times: Mapped[list[str] | None] = mapped_column(JSON, default=lambda: ["20:00"])
    long_buffer_size: Mapped[int] = mapped_column(Integer, default=1)

    # --- Planning ----------------------------------------------------------
    schedule_times: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["12:00", "18:30"])
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Paris")
    jitter_minutes: Mapped[int] = mapped_column(Integer, default=10)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    buffer_size: Mapped[int] = mapped_column(Integer, default=2)

    # --- YouTube -----------------------------------------------------------
    youtube_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    youtube_privacy: Mapped[str] = mapped_column(String(16), default="public")
    youtube_credentials: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    youtube_account: Mapped[str] = mapped_column(String(120), default="")

    # --- TikTok ------------------------------------------------------------
    tiktok_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tiktok_privacy: Mapped[str] = mapped_column(String(32), default="PUBLIC_TO_EVERYONE")
    tiktok_mode: Mapped[str] = mapped_column(String(10), default="direct")  # direct | draft
    tiktok_credentials: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tiktok_account: Mapped[str] = mapped_column(String(120), default="")

    # --- Instagram (Reels) -------------------------------------------------
    instagram_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    instagram_credentials: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    instagram_account: Mapped[str] = mapped_column(String(120), default="")

    @property
    def youtube_linked(self) -> bool:
        return bool(self.youtube_credentials)

    @property
    def tiktok_linked(self) -> bool:
        return bool(self.tiktok_credentials)

    @property
    def instagram_linked(self) -> bool:
        return bool(self.instagram_credentials)

    def platforms(self, video_format: str = VideoFormat.VERTICAL) -> list[str]:
        """Plateformes actives et connectées pour un format de vidéo."""
        out = []
        if self.youtube_enabled and self.youtube_linked:
            out.append("youtube")
        if video_format == VideoFormat.HORIZONTAL:
            return out  # les vidéos longues ne vont que sur YouTube
        if self.tiktok_enabled and self.tiktok_linked:
            out.append("tiktok")
        if self.instagram_enabled and self.instagram_linked:
            out.append("instagram")
        return out

    def formats_enabled(self) -> list[str]:
        out = []
        if self.shorts_enabled:
            out.append(VideoFormat.VERTICAL)
        if self.long_enabled and self.youtube_enabled:
            out.append(VideoFormat.HORIZONTAL)
        return out


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(200))
    hook: Mapped[str] = mapped_column(Text, default="")
    parts: Mapped[list[str]] = mapped_column(JSON, default=list)
    youtube_title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    tiktok_caption: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def full_text(self) -> str:
        return "\n\n".join(self.parts)


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(12), default=VideoFormat.VERTICAL, index=True)
    part_index: Mapped[int] = mapped_column(Integer, default=1)
    part_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default=VideoStatus.RENDERING, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumb_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    preview_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cover_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # miniature YouTube (vidéo longue)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    bg_file: Mapped[str] = mapped_column(String(500), default="")
    bg_start: Mapped[float] = mapped_column(Float, default=0.0)
    tts_chars: Mapped[int] = mapped_column(Integer, default=0)
    publish_after: Mapped[bool] = mapped_column(Boolean, default=False)
    words: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [[mot, début, fin], ...] sur la vidéo
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # UTC
    archived_from: Mapped[str] = mapped_column(String(20), default="")  # statut avant archivage
    note: Mapped[str] = mapped_column(String(300), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    tg_messages: Mapped[list[list[int]]] = mapped_column(JSON, default=list)  # [[chat_id, message_id], ...]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[int] = mapped_column(Integer, index=True)
    platform: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=PubStatus.PENDING)
    remote_id: Mapped[str] = mapped_column(String(128), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Event(Base):
    """Journal d'activité affiché dans le dashboard."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)


class KV(Base):
    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
