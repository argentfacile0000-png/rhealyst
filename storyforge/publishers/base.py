from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class PublisherError(RuntimeError):
    """Erreur de publication (message lisible pour le dashboard)."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class PublishRequest:
    video_path: str
    title: str
    description: str
    tags: list[str]
    caption: str  # légende TikTok
    language: str
    privacy: str
    mode: str = "direct"


@dataclass
class PublishResult:
    status: str  # published | processing | draft
    remote_id: str = ""
    url: str = ""
    # Identifiants mis à jour (jeton rafraîchi) à re-sauvegarder en base.
    credentials: dict[str, Any] | None = field(default=None)
