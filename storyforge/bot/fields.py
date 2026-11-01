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
            return "activé" if val else "désactivé"
        for label, v in self.options:
            if v == val:
                return label
        return str(val)

    def updates_for(self, value: Any) -> dict[str, Any]:
        return self.apply(value) if self.apply else {self.key: value}


GROUPS: list[tuple[str, str]] = [
    ("formats", "Formats"),
    ("story", "Histoire"),
    ("voice", "Voix"),
    ("video", "Vidéo"),
    ("plan", "Planning"),
    ("pub", "Publication"),
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


def _long_duration_apply(v: str) -> dict[str, Any]:
    lo, hi = (int(x) for x in v.split("-"))
    return {"long_min_minutes": lo, "long_max_minutes": hi}


def _long_times(text: str) -> dict[str, Any]:
    times = parse_times(text)
    if not times:
        raise ValueError("Donne au moins une heure, ex : 20:00")
    return {"long_schedule_times": times[:6]}


def _duration_apply(v: str) -> dict[str, Any]:
    lo, hi = (int(x) for x in v.split("-"))
    return {"min_seconds": lo, "max_seconds": hi}


COLORS = [("Blanc", "#FFFFFF"), ("Jaune", "#FFE14D"), ("Vert", "#4DFF88"), ("Cyan", "#4DE1FF"),
          ("Rose", "#FF6FD8"), ("Orange", "#FF9F1C")]

FIELDS: list[Field] = [
    # --- Formats
    Field("shorts_enabled", "Shorts verticaux (YouTube, TikTok, Reels)", "formats", "toggle"),
    Field("split_seconds", "Découpage auto des Shorts", "formats", "choice",
          options=[("Désactivé", 0), ("Parties de 60 s", 60), ("Parties de 90 s", 90), ("Parties de 120 s", 120)]),
    Field("long_enabled", "Vidéos longues YouTube (16:9)", "formats", "toggle"),
    Field("long_duration", "Durée des vidéos longues", "formats", "choice",
          options=[("5–8 min", "5-8"), ("8–12 min", "8-12"), ("12–20 min", "12-20"), ("20–30 min", "20-30")],
          current=lambda c: f"{c.long_min_minutes}-{c.long_max_minutes}",
          show=lambda c: f"{c.long_min_minutes}–{c.long_max_minutes} min", apply=_long_duration_apply),
    Field("long_schedule_times", "Horaires des vidéos longues", "formats", "text", parse=_long_times,
          show=lambda c: ", ".join(c.long_schedule_times or []) or "aucun",
          hint="Envoie les heures de publication des vidéos longues.\n<i>Exemple : 20:00</i>\n\n"
               "<i>Une minute de narration consomme environ 1 000 à 1 500 caractères ElevenLabs.</i>"),
    Field("long_buffer_size", "Réserve de vidéos longues", "formats", "choice",
          options=[("1", 1), ("2", 2), ("3", 3)]),
    # --- Histoire
    Field("name", "Nom", "story", "text", parse=_name, hint="Envoie le nouveau nom de la chaîne."),
    Field("language", "Langue", "story", "choice",
          options=[(l.label, l.code) for l in LANGUAGES.values()]),
    Field("formats", "Formats", "story", "multi",
          options=[(f.label, f.key) for f in FORMATS.values()],
          show=lambda c: ", ".join(FORMATS[f].label for f in c.formats if f in FORMATS) or "par défaut"),
    Field("narrator_gender", "Narrateur", "story", "choice",
          options=[("Au hasard", "any"), ("Femme", "female"), ("Homme", "male")]),
    Field("parts", "Parties", "story", "choice",
          options=[("1 (vidéo unique)", 1), ("2 parties", 2), ("3 parties", 3), ("4 parties", 4)]),
    Field("duration", "Durée", "story", "choice",
          options=[("30–45 s", "30-45"), ("45–60 s", "45-60"), ("65–95 s", "65-95"), ("90–120 s", "90-120"),
                   ("120–170 s", "120-170")],
          current=lambda c: f"{c.min_seconds}-{c.max_seconds}",
          show=lambda c: f"{c.min_seconds}–{c.max_seconds} s", apply=_duration_apply),
    Field("custom_instructions", "Consignes", "story", "text", parse=_custom,
          show=lambda c: "définies" if c.custom_instructions else "aucune",
          hint="Envoie des consignes éditoriales pour cette chaîne : thèmes, ton, interdits.\n"
               "<i>Exemple : histoires de mariage uniquement, ton sarcastique. Envoie « - » pour effacer.</i>"),
    # --- Voix
    Field("voice", "Voix", "voice", "voice", show=lambda c: c.voice_name or c.voice_id or "par défaut"),
    Field("voice_speed", "Vitesse voix", "voice", "choice",
          options=[("0,9×", 0.9), ("1×", 1.0), ("1,05×", 1.05), ("1,1×", 1.1), ("1,15×", 1.15), ("1,2×", 1.2)]),
    Field("speedup", "Accélération au montage", "voice", "choice",
          options=[("Aucune", 1.0), ("1,1×", 1.1), ("1,15×", 1.15), ("1,2×", 1.2), ("1,25×", 1.25), ("1,3×", 1.3),
                   ("1,4×", 1.4)]),
    Field("voice_stability", "Expressivité", "voice", "choice",
          options=[("Très expressive", 0.3), ("Expressive", 0.45), ("Équilibrée", 0.6), ("Stable", 0.75)]),
    # --- Vidéo
    Field("bg_categories", "Vidéos de fond", "video", "multi",
          show=lambda c: ", ".join(c.bg_categories) if c.bg_categories else "toutes"),
    Field("caption_style", "Style sous-titres", "video", "choice",
          options=[("1 mot à la fois", "pop"), ("3 mots, mot actif surligné", "trio")]),
    Field("caption_color", "Couleur du texte", "video", "choice", options=COLORS),
    Field("highlight_color", "Couleur surlignage", "video", "choice", options=COLORS),
    Field("caption_position", "Position du texte", "video", "choice",
          options=[("Haut", 30), ("Centre", 50), ("Bas", 68)]),
    Field("show_part_label", "Label « Partie N »", "video", "toggle"),
    Field("hook_card", "Carte d'accroche", "video", "toggle"),
    Field("music_volume", "Musique", "video", "choice",
          options=[("Aucune", 0.0), ("5 %", 0.05), ("8 %", 0.08), ("12 %", 0.12)]),
    Field("bg_volume", "Son du fond", "video", "choice",
          options=[("Coupé", 0.0), ("5 %", 0.05), ("10 %", 0.1), ("20 %", 0.2)]),
    # --- Planning
    Field("schedule_times", "Horaires", "plan", "text", parse=_times,
          show=lambda c: ", ".join(c.schedule_times) or "aucun",
          hint="Envoie les heures de publication.\n<i>Exemple : 9:00, 13:30, 19:00</i>"),
    Field("timezone", "Fuseau horaire", "plan", "text", parse=_tz, hint="Envoie le fuseau horaire.\n<i>Exemple : Europe/Paris</i>"),
    Field("jitter_minutes", "Décalage aléatoire", "plan", "choice",
          options=[("Aucune", 0), ("5 min", 5), ("10 min", 10), ("20 min", 20), ("40 min", 40)]),
    Field("approval_required", "Validation manuelle", "plan", "toggle"),
    Field("buffer_size", "Réserve", "plan", "choice",
          options=[("1", 1), ("2", 2), ("3", 3), ("5", 5), ("8", 8)]),
    # --- Publication
    Field("youtube_enabled", "YouTube", "pub", "toggle"),
    Field("youtube_privacy", "Visibilité YouTube", "pub", "choice",
          options=[("Publique", "public"), ("Non répertoriée", "unlisted"), ("Privée", "private")]),
    Field("tiktok_enabled", "TikTok", "pub", "toggle"),
    Field("tiktok_mode", "Mode TikTok", "pub", "choice",
          options=[("Publication directe", "direct"), ("Brouillon dans l'app", "draft")]),
    Field("tiktok_privacy", "Visibilité TikTok", "pub", "choice",
          options=[("Tout le monde", "PUBLIC_TO_EVERYONE"), ("Abonnés", "FOLLOWER_OF_CREATOR"),
                   ("Amis", "MUTUAL_FOLLOW_FRIENDS"), ("Moi seul", "SELF_ONLY")]),
    Field("instagram_enabled", "Instagram", "pub", "toggle"),
    Field("tiktok_account", "Pseudo TikTok", "pub", "text", parse=_tt_account,
          show=lambda c: f"@{c.tiktok_account}" if c.tiktok_account else "non défini",
          hint="Envoie ton pseudo TikTok (il sert à construire les liens des vidéos)."),
]

FIELD_MAP: dict[str, Field] = {f.key: f for f in FIELDS}


def fields_in(group: str) -> list[Field]:
    return [f for f in FIELDS if f.group == group]
