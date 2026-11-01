"""Registre des réglages modifiables d'une chaîne depuis Telegram."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from ..models import Channel
from ..scheduler import parse_times
from ..story.styles import FORMATS, LANGUAGES


@dataclass
class Field:
    key: str
    label: str
    group: str
    kind: str  # text | choice | toggle | multi | voice
    options: list[tuple[str, Any]] = field(default_factory=list)
    show: Callable[[Channel], str] | None = None
    parse: Callable[[str], dict[str, Any]] | None = None  # texte -> champs à mettre à jour
    apply: Callable[[Any], dict[str, Any]] | None = None  # valeur d'option -> champs
    current: Callable[[Channel], Any] | None = None
    hint: str = ""

    def value_text(self, ch: Channel) -> str:
        if self.show:
            return self.show(ch)
        val = self.current(ch) if self.current else getattr(ch, self.key)
        if self.kind == "toggle":
            return "✅" if val else "❌"
        for label, v in self.options:
            if v == val:
                return label
        return str(val)

    def updates_for(self, value: Any) -> dict[str, Any]:
        return self.apply(value) if self.apply else {self.key: value}


GROUPS: list[tuple[str, str]] = [
    ("story", "🧠 Histoire"),
    ("voice", "🗣 Voix"),
    ("video", "🎬 Vidéo"),
    ("plan", "⏰ Planning"),
    ("pub", "🚀 Publication"),
]


def _name(text: str) -> dict[str, Any]:
    t = text.strip()
    if not 2 <= len(t) <= 60:
        raise ValueError("Le nom doit faire entre 2 et 60 caractères")
    return {"name": t}


def _custom(text: str) -> dict[str, Any]:
    t = text.strip()
    return {"custom_instructions": "" if t in ("-", "aucune", "none") else t[:1500]}


def _times(text: str) -> dict[str, Any]:
    times = parse_times(text)
    if not times:
        raise ValueError("Donne au moins une heure, ex : 9:00, 13:30, 19:00")
    if len(times) > 24:
        raise ValueError("24 créneaux par jour maximum")
    return {"schedule_times": times}


def _tz(text: str) -> dict[str, Any]:
    t = text.strip()
    try:
        ZoneInfo(t)
    except Exception:
        raise ValueError("Fuseau inconnu (exemples : Europe/Paris, America/New_York)")
    return {"timezone": t}


def _tt_account(text: str) -> dict[str, Any]:
    return {"tiktok_account": text.strip().lstrip("@")[:100]}


def _duration_apply(v: str) -> dict[str, Any]:
    lo, hi = (int(x) for x in v.split("-"))
    return {"min_seconds": lo, "max_seconds": hi}


COLORS = [("⚪ Blanc", "#FFFFFF"), ("🟡 Jaune", "#FFE14D"), ("🟢 Vert", "#4DFF88"), ("🔵 Cyan", "#4DE1FF"),
          ("🩷 Rose", "#FF6FD8"), ("🟠 Orange", "#FF9F1C")]

FIELDS: list[Field] = [
    # --- Histoire
    Field("name", "Nom", "story", "text", parse=_name, hint="Envoie le nouveau nom de la chaîne."),
    Field("language", "Langue", "story", "choice",
          options=[(f"{l.flag} {l.label}", l.code) for l in LANGUAGES.values()]),
    Field("formats", "Formats d'histoires", "story", "multi",
          options=[(f"{f.emoji} {f.label}", f.key) for f in FORMATS.values()],
          show=lambda c: ", ".join(FORMATS[f].emoji for f in c.formats if f in FORMATS) or "défaut"),
    Field("narrator_gender", "Narrateur", "story", "choice",
          options=[("🎲 Au hasard", "any"), ("👩 Femme", "female"), ("👨 Homme", "male")]),
    Field("parts", "Nombre de parties", "story", "choice",
          options=[("1 (vidéo unique)", 1), ("2 parties", 2), ("3 parties", 3), ("4 parties", 4)]),
    Field("duration", "Durée par vidéo", "story", "choice",
          options=[("30-45 s", "30-45"), ("45-60 s", "45-60"), ("65-95 s", "65-95"), ("90-120 s", "90-120"),
                   ("120-170 s", "120-170")],
          current=lambda c: f"{c.min_seconds}-{c.max_seconds}",
          show=lambda c: f"{c.min_seconds}-{c.max_seconds} s", apply=_duration_apply),
    Field("custom_instructions", "Consignes perso", "story", "text", parse=_custom,
          show=lambda c: "✍️ définies" if c.custom_instructions else "—",
          hint="Envoie des consignes éditoriales pour cette chaîne (thèmes, ton, interdits…).\n"
               "Ex : « Histoires de mariage uniquement, ton sarcastique ». Envoie « - » pour effacer."),
    # --- Voix
    Field("voice", "Voix ElevenLabs", "voice", "voice", show=lambda c: c.voice_name or c.voice_id or "défaut"),
    Field("voice_speed", "Vitesse voix", "voice", "choice",
          options=[("0.9×", 0.9), ("1.0×", 1.0), ("1.05×", 1.05), ("1.1×", 1.1), ("1.15×", 1.15), ("1.2×", 1.2)]),
    Field("speedup", "Accélération montage", "voice", "choice",
          options=[("Aucune", 1.0), ("1.1×", 1.1), ("1.15×", 1.15), ("1.2×", 1.2), ("1.25×", 1.25), ("1.3×", 1.3),
                   ("1.4×", 1.4)]),
    Field("voice_stability", "Stabilité (émotion)", "voice", "choice",
          options=[("Très expressive 0.3", 0.3), ("Expressive 0.45", 0.45), ("Équilibrée 0.6", 0.6),
                   ("Stable 0.75", 0.75)]),
    # --- Vidéo
    Field("bg_categories", "Fonds vidéo", "video", "multi",
          show=lambda c: ", ".join(c.bg_categories) if c.bg_categories else "toutes catégories"),
    Field("caption_style", "Style sous-titres", "video", "choice",
          options=[("💥 1 mot (pop)", "pop"), ("🔤 3 mots surlignés", "trio")]),
    Field("caption_color", "Couleur du texte", "video", "choice", options=COLORS),
    Field("highlight_color", "Couleur surlignage", "video", "choice", options=COLORS),
    Field("caption_position", "Position du texte", "video", "choice",
          options=[("⬆️ Haut (30%)", 30), ("⏺ Centre (50%)", 50), ("⬇️ Bas (68%)", 68)]),
    Field("show_part_label", "Label « PARTIE N »", "video", "toggle"),
    Field("hook_card", "Carte d'accroche", "video", "toggle"),
    Field("music_volume", "Musique de fond", "video", "choice",
          options=[("🔇 Aucune", 0.0), ("🔈 5%", 0.05), ("🔉 8%", 0.08), ("🔊 12%", 0.12)]),
    Field("bg_volume", "Son de la vidéo de fond", "video", "choice",
          options=[("🔇 Coupé", 0.0), ("🔈 5%", 0.05), ("🔉 10%", 0.1), ("🔊 20%", 0.2)]),
    # --- Planning
    Field("schedule_times", "Horaires", "plan", "text", parse=_times,
          show=lambda c: " · ".join(c.schedule_times) or "aucun",
          hint="Envoie les heures de publication, ex : <code>9:00, 13:30, 19:00</code>"),
    Field("timezone", "Fuseau horaire", "plan", "text", parse=_tz, hint="Ex : Europe/Paris"),
    Field("jitter_minutes", "Variation aléatoire", "plan", "choice",
          options=[("Aucune", 0), ("± 5 min", 5), ("± 10 min", 10), ("± 20 min", 20), ("± 40 min", 40)]),
    Field("approval_required", "Validation manuelle", "plan", "toggle"),
    Field("buffer_size", "Vidéos d'avance", "plan", "choice",
          options=[("1", 1), ("2", 2), ("3", 3), ("5", 5), ("8", 8)]),
    # --- Publication
    Field("youtube_enabled", "YouTube actif", "pub", "toggle"),
    Field("youtube_privacy", "Visibilité YouTube", "pub", "choice",
          options=[("🌍 Publique", "public"), ("🔗 Non répertoriée", "unlisted"), ("🔒 Privée", "private")]),
    Field("tiktok_enabled", "TikTok actif", "pub", "toggle"),
    Field("tiktok_mode", "Mode TikTok", "pub", "choice",
          options=[("🚀 Publication directe", "direct"), ("📥 Brouillon (inbox)", "draft")]),
    Field("tiktok_privacy", "Visibilité TikTok", "pub", "choice",
          options=[("🌍 Tout le monde", "PUBLIC_TO_EVERYONE"), ("👥 Abonnés", "FOLLOWER_OF_CREATOR"),
                   ("🤝 Amis", "MUTUAL_FOLLOW_FRIENDS"), ("🔒 Moi seul", "SELF_ONLY")]),
    Field("tiktok_account", "@ TikTok (liens)", "pub", "text", parse=_tt_account,
          show=lambda c: f"@{c.tiktok_account}" if c.tiktok_account else "—",
          hint="Envoie ton @pseudo TikTok (sert à construire les liens des vidéos)."),
]

FIELD_MAP: dict[str, Field] = {f.key: f for f in FIELDS}


def fields_in(group: str) -> list[Field]:
    return [f for f in FIELDS if f.group == group]
