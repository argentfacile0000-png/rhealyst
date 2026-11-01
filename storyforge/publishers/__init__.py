from .base import PublisherError, PublishRequest, PublishResult

PLATFORM_LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}

__all__ = ["PLATFORM_LABELS", "PublishRequest", "PublishResult", "PublisherError"]
