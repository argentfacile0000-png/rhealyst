"""Interface de notification (implémentée par le bot Telegram) + version muette."""

from __future__ import annotations

from typing import Protocol

STEPS: list[tuple[str, str, str]] = [
    ("story", "🧠", "Écriture de l'histoire"),
    ("voice", "🎙", "Voix off ElevenLabs"),
    ("background", "🎞", "Séquence de fond"),
    ("render", "🎬", "Montage vidéo"),
    ("finish", "✨", "Finalisation"),
]


class Progress(Protocol):
    async def update(self, step: str, state: str, detail: str = "", pct: float | None = None) -> None: ...

    async def finish(self, text: str, ok: bool = True) -> None: ...


class Notifier(Protocol):
    async def progress(self, title: str, chat_id: int | None = None) -> Progress: ...

    async def notify(self, text: str, level: str = "info", channel_id: int | None = None) -> None: ...

    async def video_for_approval(self, video_id: int) -> None: ...

    async def video_published(self, video_id: int) -> None: ...


class NullProgress:
    async def update(self, step: str, state: str, detail: str = "", pct: float | None = None) -> None:
        return None

    async def finish(self, text: str, ok: bool = True) -> None:
        return None


class NullNotifier:
    async def progress(self, title: str, chat_id: int | None = None) -> Progress:
        return NullProgress()

    async def notify(self, text: str, level: str = "info", channel_id: int | None = None) -> None:
        return None

    async def video_for_approval(self, video_id: int) -> None:
        return None

    async def video_published(self, video_id: int) -> None:
        return None
