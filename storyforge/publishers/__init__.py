from .base import PublisherError, PublishRequest, PublishResult

PLATFORM_LABELS = {"youtube": "▶️ YouTube", "tiktok": "🎵 TikTok"}

__all__ = ["PLATFORM_LABELS", "PublishRequest", "PublishResult", "PublisherError"]
