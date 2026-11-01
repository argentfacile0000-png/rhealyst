"""Mise en forme du dashboard Telegram — déclinaison du Minimal Design System.

Principes appliqués aux messages Telegram :
- hiérarchie par la typographie (gras = titres et valeurs, italique = texte secondaire) ;
- beaucoup d'air (lignes vides entre sections), aucun séparateur décoratif ;
- palette neutre : pas d'emoji décoratif, statuts en glyphes monochromes accompagnés d'un libellé ;
- boutons en texte simple, verbes d'action, « ✓ » pour l'option sélectionnée.
"""

from __future__ import annotations

import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from ..models import PubStatus, VideoStatus

ON = "●"
OFF = "○"
CHECK = "✓"
CROSS = "✕"
DOT = " · "
BACK = "← Retour"
HOME = "Accueil"

# statut -> (glyphe, libellé)
VIDEO_STATUS = {
    VideoStatus.RENDERING: ("…", "En fabrication"),
    VideoStatus.PENDING_APPROVAL: ("◐", "À valider"),
    VideoStatus.READY: ("●", "Prête"),
    VideoStatus.PUBLISHING: ("…", "Publication en cours"),
    VideoStatus.PUBLISHED: ("✓", "Publiée"),
    VideoStatus.FAILED: ("✕", "Échec"),
    VideoStatus.DISCARDED: ("–", "Jetée"),
}

PUB_STATUS = {
    PubStatus.PENDING: "en attente",
    PubStatus.UPLOADING: "envoi en cours",
    PubStatus.PROCESSING: "en traitement",
    PubStatus.PUBLISHED: "publiée",
    PubStatus.DRAFT: "brouillon envoyé",
    PubStatus.FAILED: "échec",
}

PLATFORM = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def title(text: str, context: str = "") -> str:
    """Titre d'écran : « <b>Titre</b> · contexte »."""
    return f"<b>{esc(text)}</b>" + (f"{DOT}{esc(context)}" if context else "")


def row(label: str, value: Any) -> str:
    """Ligne libellé / valeur (la valeur porte l'emphase)."""
    return f"{esc(label)}  <b>{esc(value)}</b>"


def strip_platform(message: str, platform: str) -> str:
    """« TikTok : quota » -> « quota » quand le nom de la plateforme est déjà affiché."""
    prefix = f"{platform} : "
    return message[len(prefix):] if message.startswith(prefix) else message


def muted(text: str) -> str:
    return f"<i>{esc(text)}</i>"


def bar(frac: float, n: int = 12) -> str:
    frac = max(0.0, min(1.0, frac))
    full = int(round(frac * n))
    return "━" * full + "─" * (n - full)


def short(text: str, n: int = 48) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def mmss(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def hhmm(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d}"


def num(n: int | float) -> str:
    """12 345 -> « 12 345 » (espace fine insécable)."""
    return f"{int(n):,}".replace(",", " ")


def kfmt(n: int | float) -> str:
    n = float(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M".replace(".", ",")
    if n >= 1000:
        return f"{n / 1000:.1f} k".replace(".", ",")
    return str(int(n))


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    return f"{n} {word if n <= 1 else (plural_word or word + 's')}"


Btn = tuple[str, str] | InlineKeyboardButton


def kb(rows: list[list[Btn]]) -> InlineKeyboardMarkup:
    out = []
    for r in rows:
        line = []
        for b in r:
            if isinstance(b, InlineKeyboardButton):
                line.append(b)
            else:
                text, data = b
                if data.startswith(("http://", "https://")):
                    line.append(InlineKeyboardButton(text, url=data))
                else:
                    line.append(InlineKeyboardButton(text, callback_data=data[:64]))
        if line:
            out.append(line)
    return InlineKeyboardMarkup(out)


def webapp_button(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, web_app=WebAppInfo(url=url))


def nav(back: str | None = None, back_label: str = BACK) -> list[Btn]:
    out: list[Btn] = []
    if back:
        out.append((back_label, back))
    out.append((HOME, "h"))
    return out
