"""Banque de bandes son : une piste tirée au hasard (parmi celles choisies pour la chaîne),
lue à partir d'un moment au hasard, avec fondu d'entrée et de sortie.

Organisation : data/music/<fichier>.mp3 (mp3, m4a, aac, wav, ogg, opus, flac).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path

from .background import BackgroundBank

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"}


@dataclass
class MusicPick:
    path: Path
    start: float  # point de départ dans la piste (s)
    duration: float  # durée totale de la piste (s)

    @property
    def name(self) -> str:
        return self.path.name


class MusicBank:
    def __init__(self, root: Path, *, ffprobe: str = "ffprobe"):
        self.root = root
        self._probe = BackgroundBank(root, ffprobe=ffprobe)  # réutilise le cache des durées

    def tracks(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(
            (p for p in self.root.rglob("*")
             if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith(".")),
            key=lambda p: p.relative_to(self.root).as_posix().lower(),
        )

    def name_of(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def names(self) -> list[str]:
        return [self.name_of(p) for p in self.tracks()]

    def resolve(self, name: str) -> Path | None:
        """Chemin d'une piste de la banque (jamais en dehors du dossier)."""
        for p in self.tracks():
            if self.name_of(p) == name:
                return p
        return None

    async def duration(self, path: Path) -> float:
        return await self._probe.duration(path)

    async def summary(self) -> list[tuple[str, float]]:
        out = []
        for p in self.tracks():
            try:
                out.append((self.name_of(p), await self.duration(p)))
            except Exception as exc:  # fichier illisible : on le signale sans bloquer la liste
                log.warning("Piste illisible %s : %s", p.name, exc)
                out.append((self.name_of(p), 0.0))
        return out

    async def pick(self, selected: list[str] | None, needed: float, rng: random.Random | None = None) -> MusicPick | None:
        """Piste au hasard parmi la sélection (vide = toutes) et départ au hasard couvrant `needed` secondes."""
        rng = rng or random.Random()
        tracks = self.tracks()
        if selected:
            chosen = [p for p in tracks if self.name_of(p) in set(selected)]
            if not chosen and tracks:
                log.warning("Pistes choisies introuvables (%s) : tirage parmi toute la banque", ", ".join(selected))
            tracks = chosen or tracks
        rng.shuffle(tracks)
        for path in tracks:
            try:
                dur = await self.duration(path)
            except Exception as exc:
                log.warning("Piste ignorée %s : %s", path.name, exc)
                continue
            if dur <= 1:
                continue
            # Assez longue : départ au hasard. Sinon la piste est bouclée depuis le début.
            start = rng.uniform(0, dur - needed) if dur > needed + 2 else 0.0
            return MusicPick(path, round(start, 2), dur)
        return None
