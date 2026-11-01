"""Sous-titres ASS "mot par mot" façon TikTok (texte blanc épais, contour noir, effet pop)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..tts.timing import Word

_STRIP_CHARS = ".,;:!?\"“”«»()[]{}…—–-*_/\\"


@dataclass
class CaptionStyle:
    mode: str = "pop"  # pop = 1 mot à la fois | trio = groupes de 3 mots, mot actif surligné
    font: str = "Montserrat ExtraBold"
    size: int = 0  # 0 = automatique selon le mode (pop: 112, trio: 92)
    color: str = "#FFFFFF"
    highlight: str = "#FFE14D"
    outline: int = 8
    shadow: int = 3
    position: int = 50  # % de la hauteur (centre du texte)
    uppercase: bool = False
    label_font: str = "Montserrat Black"
    label_color: str = "#3D8BFF"
    label_size: int = 92


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (format ASS)."""
    h = hex_color.lstrip("#")
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def clean_word(text: str) -> str:
    w = text.strip(_STRIP_CHARS + " ")
    return w.replace("{", "(").replace("}", ")").replace("\\", "")


@dataclass
class Cue:
    text: str
    start: float
    end: float


def display_words(words: list[Word], *, uppercase: bool = False) -> list[Cue]:
    """Nettoie la ponctuation ; les "mots" vides (ex: « ? » isolé en français) sont fusionnés."""
    cues: list[Cue] = []
    for w in words:
        txt = clean_word(w.text)
        if not txt:
            if cues:
                cues[-1].end = max(cues[-1].end, w.end)
            continue
        cues.append(Cue(txt.upper() if uppercase else txt, w.start, w.end))
    return cues


def _close_gaps(cues: list[Cue], max_hold: float = 0.6) -> None:
    """Chaque mot reste affiché jusqu'au suivant (pas de clignotement), sauf longue pause."""
    for a, b in zip(cues, cues[1:]):
        if b.start - a.end <= max_hold:
            a.end = b.start
        else:
            a.end = a.end + 0.2
    if cues:
        cues[-1].end += 0.4


def _header(width: int, height: int, style: CaptionStyle) -> str:
    primary = ass_color(style.color)
    label = ass_color(style.label_color)
    size = style.size or (92 if style.mode == "trio" else 112)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,{style.font},{size},{primary},{primary},&H00000000,&H78000000,0,0,0,0,100,100,0,0,1,{style.outline},{style.shadow},5,60,60,0,1
Style: Label,{style.label_font},{style.label_size},{label},{label},&H00FFFFFF,&H00000000,0,0,0,0,100,100,2,0,1,4,0,8,60,60,{int(height * 0.085)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(
    words: list[Word],
    *,
    width: int = 1080,
    height: int = 1920,
    style: CaptionStyle | None = None,
    label: str | None = None,
    duration: float | None = None,
    captions_from: float = 0.0,
) -> str:
    """Construit le fichier .ass complet.

    - `label` : texte fixe en haut (ex: "PART 1"), affiché toute la vidéo.
    - `captions_from` : n'affiche les sous-titres qu'à partir de ce temps (carte d'accroche).
    """
    style = style or CaptionStyle()
    cues = display_words(words, uppercase=style.uppercase)
    _close_gaps(cues)
    total = duration or (cues[-1].end if cues else 0.0)
    x = width // 2
    y = int(height * style.position / 100)
    lines = [_header(width, height, style)]

    if label:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(total)},Label,,0,0,0,,{clean_word(label) or label}")

    pop = r"{\an5\pos(%d,%d)\fscx72\fscy72\t(0,70,\fscx110\fscy110)\t(70,140,\fscx100\fscy100)}" % (x, y)
    visible = [c for c in cues if c.end > captions_from]
    for c in visible:
        c.start = max(c.start, captions_from)

    if style.mode == "trio":
        hl = ass_color(style.highlight)
        base = ass_color(style.color)
        groups: list[list[Cue]] = []
        for c in visible:
            if not groups or len(groups[-1]) >= 3 or (groups[-1] and c.start - groups[-1][-1].end > 0.5):
                groups.append([])
            groups[-1].append(c)
        for g in groups:
            for i, active in enumerate(g):
                parts = []
                for j, c in enumerate(g):
                    if j == i:
                        parts.append(r"{\c%s\fscx112\fscy112}%s{\c%s\fscx100\fscy100}" % (hl, c.text, base))
                    else:
                        parts.append(c.text)
                text = r"{\an5\pos(%d,%d)}" % (x, y) + " ".join(parts)
                end = g[i + 1].start if i + 1 < len(g) else active.end
                lines.append(f"Dialogue: 0,{ass_time(active.start)},{ass_time(end)},Word,,0,0,0,,{text}")
    else:
        for c in visible:
            if c.end - c.start < 0.02:
                continue
            lines.append(f"Dialogue: 0,{ass_time(c.start)},{ass_time(c.end)},Word,,0,0,0,,{pop}{c.text}")

    return "\n".join(lines) + "\n"


def hook_end_time(words: list[Word], hook: str) -> float:
    """Temps de fin de la phrase d'accroche (pour la carte affichée pendant la lecture du titre)."""
    n = len([w for w in re.split(r"\s+", hook.strip()) if w])
    if n and len(words) >= n:
        return words[n - 1].end
    for w in words:
        if w.text.rstrip().endswith((".", "!", "?", "…")):
            return w.end
    return words[0].end if words else 0.0
