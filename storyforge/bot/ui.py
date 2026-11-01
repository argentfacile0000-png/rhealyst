"""Petits utilitaires de mise en forme pour le dashboard Telegram."""

from __future__ import annotations

import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from ..models import PubStatus, VideoStatus

SEP = "━━━━━━━━━━━━━━━━━━━━━━"

VIDEO_STATUS = {
    VideoStatus.RENDERING: ("⚙️", "En fabrication"),
    VideoStatus.PENDING_APPROVAL: ("🟡", "À valider"),
    VideoStatus.READY: ("🟢", "Prête"),
    VideoStatus.PUBLISHING: ("📤", "Publication…"),
    VideoStatus.PUBLISHED: ("🚀", "Publiée"),
    VideoStatus.FAILED: ("🔴", "Échec"),
    VideoStatus.DISCARDED: ("⚫", "Jetée"),
}

PUB_STATUS = {
    PubStatus.PENDING: "⏳",
    PubStatus.UPLOADING: "📤",
    PubStatus.PROCESSING: "⚙️",
    PubStatus.PUBLISHED: "✅",
    PubStatus.DRAFT: "📥",
    PubStatus.FAILED: "❌",
}

PLATFORM = {"youtube": "▶️ YouTube", "tiktok": "🎵 TikTok"}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def bar(frac: float, n: int = 10) -> str:
    frac = max(0.0, min(1.0, frac))
    full = int(round(frac * n))
    return "▰" * full + "▱" * (n - full)


def spark(values: list[int]) -> str:
    ticks = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    hi = max(values) or 1
    return "".join(ticks[min(len(ticks) - 1, int(v / hi * (len(ticks) - 1)))] if v else "·" for v in values)


def short(text: str, n: int = 48) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def mmss(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def kfmt(n: int | float) -> str:
    n = float(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(int(n))


Btn = tuple[str, str] | InlineKeyboardButton


def kb(rows: list[list[Btn]]) -> InlineKeyboardMarkup:
    out = []
    for row in rows:
        r = []
        for b in row:
            if isinstance(b, InlineKeyboardButton):
                r.append(b)
            else:
                text, data = b
                if data.startswith(("http://", "https://")):
                    r.append(InlineKeyboardButton(text, url=data))
                else:
                    r.append(InlineKeyboardButton(text, callback_data=data[:64]))
        if r:
            out.append(r)
    return InlineKeyboardMarkup(out)


def webapp_button(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, web_app=WebAppInfo(url=url))
