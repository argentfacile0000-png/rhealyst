"""Banque de vidéos de fond : vidéo choisie au hasard, puis séquence au hasard à l'intérieur.

Organisation : data/backgrounds/<catégorie>/<fichier>.mp4
(ex: cooking/, minecraft/, gta/, satisfying/...). Les fichiers posés directement
à la racine sont rangés dans la catégorie "general".

Exemple : fond de 43 min, narration de 1 min 38 -> on tire un point de départ
au hasard entre ~5 s et 43:00 - 1:38 - 5 s, en évitant les passages déjà utilisés récemment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import probe_duration

log = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
ROOT_CATEGORY = "general"


@dataclass
class Segment:
    path: Path
    start: float
    duration: float
    source_duration: float
    loop: bool = False  # vrai si la source est plus courte que la narration

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class BankFile:
    path: Path
    category: str
    duration: float
    orientation: str = ""  # vertical | horizontal | square | "" (inconnue)


def orientation_of(width: int, height: int) -> str:
    if not width or not height:
        return ""
    ratio = width / height
    return "horizontal" if ratio >= 1.2 else "vertical" if ratio <= 0.85 else "square"


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def choose_start(
    source_duration: float,
    needed: float,
    *,
    avoid: list[tuple[float, float]] | None = None,
    rng: random.Random | None = None,
    margin: float = 5.0,
    attempts: int = 30,
) -> float:
    """Tire un départ aléatoire tel que [start, start+needed] tienne dans la vidéo source.

    Évite au maximum les intervalles `avoid` (passages déjà utilisés).
    """
    rng = rng or random.Random()
    if source_duration <= needed:
        return 0.0
    # Marge de début/fin (génériques, fondus...) si la vidéo est assez longue.
    m = min(margin, max(0.0, (source_duration - needed) / 4))
    lo, hi = m, source_duration - needed - m
    if hi <= lo:
        lo, hi = 0.0, source_duration - needed
    avoid = sorted(avoid or [])
    # Plages de départ libres = [lo, hi] privé de ]a - needed, b[ pour chaque passage utilisé.
    free: list[tuple[float, float]] = [(lo, hi)]
    for a, b in avoid:
        cut_lo, cut_hi = a - needed, b
        nxt: list[tuple[float, float]] = []
        for f0, f1 in free:
            if cut_hi <= f0 or cut_lo >= f1:
                nxt.append((f0, f1))
                continue
            if cut_lo > f0:
                nxt.append((f0, cut_lo))
            if cut_hi < f1:
                nxt.append((cut_hi, f1))
        free = nxt
    total = sum(f1 - f0 for f0, f1 in free)
    if total > 0:
        r = rng.uniform(0, total)
        for f0, f1 in free:
            if r <= f1 - f0:
                return f0 + r
            r -= f1 - f0
        return free[-1][1]
    # Tout a déjà servi : on minimise le recouvrement.
    best_start, best_overlap = lo, float("inf")
    for _ in range(attempts):
        start = rng.uniform(lo, hi)
        ov = sum(_overlap(start, start + needed, a, b) for a, b in avoid)
        if ov == 0:
            return start
        if ov < best_overlap:
            best_start, best_overlap = start, ov
    return best_start


class BackgroundBank:
    def __init__(self, root: Path, *, ffprobe: str = "ffprobe"):
        self.root = root
        self.ffprobe = ffprobe
        self._cache_file = root / ".durations.json"
        self._cache: dict[str, dict] | None = None
        self._lock = asyncio.Lock()

    # --- inventaire --------------------------------------------------------
    def _iter_files(self) -> list[tuple[Path, str]]:
        if not self.root.exists():
            return []
        out: list[tuple[Path, str]] = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS or p.name.startswith("."):
                continue
            rel = p.relative_to(self.root)
            category = rel.parts[0] if len(rel.parts) > 1 else ROOT_CATEGORY
            out.append((p, category))
        return out

    def categories(self) -> dict[str, list[Path]]:
        cats: dict[str, list[Path]] = {}
        for p, c in self._iter_files():
            cats.setdefault(c, []).append(p)
        return cats

    def _load_cache(self) -> dict[str, dict]:
        if self._cache is None:
            try:
                self._cache = json.loads(self._cache_file.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}
        return self._cache

    def _save_cache(self) -> None:
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            self._cache_file.write_text(json.dumps(self._cache or {}, indent=0), "utf-8")
        except OSError:
            log.warning("Impossible d'écrire le cache des durées")

    async def info(self, path: Path) -> dict:
        """Durée et orientation (largeur/hauteur affichées, rotation des vidéos de téléphone comprise), en cache."""
        async with self._lock:
            cache = self._load_cache()
            st = path.stat()
            key = str(path.resolve())
            entry = cache.get(key)
            fresh = entry and entry.get("size") == st.st_size and entry.get("mtime") == int(st.st_mtime)
            if fresh and "orientation" in entry:
                return entry
            dur = float(entry["duration"]) if fresh else await probe_duration(path, self.ffprobe)
            orientation = ""
            if path.suffix.lower() in VIDEO_EXTS:
                try:
                    orientation = await _probe_orientation(path, self.ffprobe)
                except Exception as exc:
                    log.debug("Orientation inconnue pour %s : %s", path.name, exc)
            entry = {"size": st.st_size, "mtime": int(st.st_mtime), "duration": dur, "orientation": orientation}
            cache[key] = entry
            self._save_cache()
            return entry

    async def duration(self, path: Path) -> float:
        return float((await self.info(path))["duration"])

    async def scan(self) -> list[BankFile]:
        files = []
        for p, c in self._iter_files():
            try:
                meta = await self.info(p)
                files.append(BankFile(p, c, float(meta["duration"]), meta.get("orientation", "")))
            except Exception as exc:  # fichier corrompu : on l'ignore
                log.warning("Fond ignoré %s: %s", p, exc)
        return files

    async def summary(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for f in await self.scan():
            s = out.setdefault(f.category, {"files": 0, "seconds": 0.0, "vertical": 0, "horizontal": 0})
            s["files"] += 1
            s["seconds"] += f.duration
            if f.orientation in ("vertical", "horizontal"):
                s[f.orientation] += 1
        return out

    # --- tirage ------------------------------------------------------------
    async def pick(
        self,
        needed: float,
        *,
        categories: list[str] | None = None,
        recent: list[tuple[str, float, float]] | None = None,
        rng: random.Random | None = None,
        orientation: str | None = None,
    ) -> Segment:
        """Choisit une vidéo au hasard dans les catégories puis une séquence au hasard.

        `recent` = [(chemin, début, fin)] des passages déjà utilisés à éviter.
        `orientation` (vertical | horizontal) : les fonds de la même orientation que la vidéo à monter passent
        en priorité (pas de recadrage) ; à défaut, n'importe quel fond assez long.
        """
        rng = rng or random.Random()
        files = await self.scan()
        if categories:
            wanted = set(categories)
            filtered = [f for f in files if f.category in wanted]
            files = filtered or files
        files = [f for f in files if f.duration > 1]
        if not files:
            raise FileNotFoundError(
                f"Aucune vidéo de fond dans {self.root}. Ajoute des .mp4 dans data/backgrounds/<categorie>/"
            )

        long_enough = [f for f in files if f.duration >= needed + 1]
        if orientation:
            same = [f for f in long_enough if f.orientation == orientation]
            if same:
                long_enough = same
            else:
                log.info("Aucun fond %s assez long : un fond d'une autre orientation sera recadré", orientation)
        if long_enough:
            chosen = rng.choice(long_enough)
            avoid = [(a, b) for (p, a, b) in (recent or []) if Path(p).resolve() == chosen.path.resolve()]
            start = choose_start(chosen.duration, needed, avoid=avoid, rng=rng)
            return Segment(chosen.path, start, needed, chosen.duration, loop=False)

        pool = [f for f in files if f.orientation == orientation] if orientation else []
        chosen = max(pool or files, key=lambda f: f.duration)
        log.warning("Aucun fond assez long (%.0fs requis) — %s sera bouclé", needed, chosen.path.name)
        return Segment(chosen.path, 0.0, needed, chosen.duration, loop=True)


async def _probe_orientation(path: Path, ffprobe: str = "ffprobe") -> str:
    """Orientation à l'affichage : tient compte de la rotation (vidéos filmées au téléphone)."""
    from .ffmpeg import ffprobe_json

    info = await ffprobe_json(path, ffprobe)
    for st in info.get("streams", []):
        if st.get("codec_type") != "video" or not st.get("width"):
            continue
        w, h = int(st["width"]), int(st["height"])
        rotation = 0
        try:
            rotation = int(float((st.get("tags") or {}).get("rotate", 0)))
        except ValueError:
            pass
        for side in st.get("side_data_list") or []:
            if "rotation" in side:
                rotation = int(float(side["rotation"]))
        if abs(rotation) % 180 == 90:
            w, h = h, w
        return orientation_of(w, h)
    return ""
