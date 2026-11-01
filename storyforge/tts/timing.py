"""Horodatage mot à mot et resserrage des silences (rythme "storytime" sans temps mort)."""

from __future__ import annotations

import array
import sys
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Word:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}


def words_from_alignment(
    characters: list[str],
    starts: list[float],
    ends: list[float],
    offset: float = 0.0,
) -> list[Word]:
    """Regroupe l'alignement caractère par caractère d'ElevenLabs en mots."""
    words: list[Word] = []
    buf: list[str] = []
    w_start = w_end = 0.0
    for ch, s, e in zip(characters, starts, ends):
        if ch.isspace():
            if buf:
                words.append(Word("".join(buf), w_start + offset, w_end + offset))
                buf = []
            continue
        if not buf:
            w_start = float(s)
        buf.append(ch)
        w_end = float(e)
    if buf:
        words.append(Word("".join(buf), w_start + offset, w_end + offset))
    # Garantit des temps monotones et des durées non nulles.
    for i, w in enumerate(words):
        if w.end <= w.start:
            w.end = w.start + 0.05
        if i and w.start < words[i - 1].start:
            w.start = words[i - 1].start
    return words


def scale_words(words: list[Word], factor: float) -> list[Word]:
    """Adapte les temps après une accélération audio (factor = vitesse, ex 1.2)."""
    if factor <= 0 or factor == 1:
        return [Word(w.text, w.start, w.end) for w in words]
    return [Word(w.text, w.start / factor, w.end / factor) for w in words]


def _read_mono16(path: Path) -> tuple[array.array, int]:
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("WAV mono 16 bits attendu")
        sr = wf.getframerate()
        data = array.array("h")
        data.frombytes(wf.readframes(wf.getnframes()))
    if sys.byteorder == "big":
        data.byteswap()
    return data, sr


def _write_mono16(path: Path, data: array.array, sr: int) -> None:
    out = array.array("h", data)
    if sys.byteorder == "big":
        out.byteswap()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(out.tobytes())


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def concat_wavs(paths: list[Path], dst: Path) -> list[float]:
    """Concatène des WAV mono 16 bits ; renvoie l'offset de début de chaque fichier."""
    out = array.array("h")
    offsets: list[float] = []
    sr_ref = None
    for p in paths:
        data, sr = _read_mono16(p)
        if sr_ref is None:
            sr_ref = sr
        elif sr != sr_ref:
            raise ValueError("Fréquences d'échantillonnage différentes")
        offsets.append(len(out) / sr)
        out.extend(data)
    _write_mono16(dst, out, sr_ref or 44100)
    return offsets


def compute_keep_segments(
    words: list[Word], total: float, *, max_pause: float, lead: float, tail: float
) -> list[tuple[float, float]]:
    if not words:
        return [(0.0, total)]
    keep: list[tuple[float, float]] = []
    cursor = max(0.0, words[0].start - lead)
    for a, b in zip(words, words[1:]):
        gap = b.start - a.end
        if gap > max_pause:
            cut_a = a.end + max_pause / 2
            cut_b = b.start - max_pause / 2
            if cut_a > cursor:
                keep.append((cursor, cut_a))
            cursor = max(cursor, cut_b)
    end = min(total, words[-1].end + tail)
    if end > cursor:
        keep.append((cursor, end))
    return keep


def remap_time(t: float, keep: list[tuple[float, float]], out_starts: list[float] | None = None) -> float:
    """Convertit un temps de l'audio source en temps de l'audio resserré.

    `out_starts[k]` = position (s) du début du segment k dans l'audio de sortie
    (par défaut : segments mis bout à bout sans recouvrement).
    """
    if out_starts is None:
        out_starts, acc = [], 0.0
        for a, b in keep:
            out_starts.append(acc)
            acc += b - a
    last_end = 0.0
    for (a, b), start in zip(keep, out_starts):
        if t < a:
            return max(last_end, start)
        if t <= b:
            return start + (t - a)
        last_end = start + (b - a)
    return last_end


def tighten_pauses(
    src: Path,
    dst: Path,
    words: list[Word],
    *,
    max_pause: float = 0.22,
    lead: float = 0.06,
    tail: float = 0.35,
    crossfade_ms: float = 8.0,
) -> list[Word]:
    """Raccourcit chaque silence entre deux mots à `max_pause` secondes max.

    Les vidéos virales n'ont quasiment aucun blanc : ça maintient l'attention.
    Retourne les mots recalés sur le nouvel audio.
    """
    data, sr = _read_mono16(src)
    total = len(data) / sr
    keep = compute_keep_segments(words, total, max_pause=max_pause, lead=lead, tail=tail)

    fade = max(1, int(sr * crossfade_ms / 1000))
    out = array.array("h")
    out_starts: list[float] = []
    for a, b in keep:
        seg = data[int(a * sr) : int(b * sr)]
        if out and len(seg) > fade and len(out) > fade:
            # fondu enchaîné très court pour éviter les clics
            start_idx = len(out) - fade
            for i in range(fade):
                k = i / fade
                idx = start_idx + i
                out[idx] = int(out[idx] * (1 - k) + seg[i] * k)
            out.extend(seg[fade:])
        else:
            start_idx = len(out)
            out.extend(seg)
        out_starts.append(start_idx / sr)
    _write_mono16(dst, out, sr)

    new_words = []
    for w in words:
        s = remap_time(w.start, keep, out_starts)
        e = remap_time(w.end, keep, out_starts)
        new_words.append(Word(w.text, s, max(e, s + 0.04)))
    return new_words
