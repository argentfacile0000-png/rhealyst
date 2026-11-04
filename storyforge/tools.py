"""Outils externes : yt-dlp (téléchargement de fonds et de musiques par lien)."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Settings


def ytdlp_command() -> list[str] | None:
    """Commande yt-dlp : installée avec StoryForge (même environnement Python), dans le PATH, ou en module."""
    local = Path(sys.executable).with_name("yt-dlp")  # .venv/bin/yt-dlp (le service n'a pas .venv/bin dans son PATH)
    if local.is_file():
        return [str(local)]
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return None


def js_runtime_args() -> list[str]:
    """YouTube exige un moteur JavaScript : Deno par défaut, sinon Node.js ou Bun s'ils sont installés."""
    if shutil.which("deno"):
        return []
    for name in ("node", "bun"):
        path = shutil.which(name)
        if path:
            return ["--js-runtimes", f"{name}:{path}"]
    nvm = sorted(Path.home().glob(".nvm/versions/node/*/bin/node"))  # Node installé avec nvm
    if nvm:
        return ["--js-runtimes", f"node:{nvm[-1]}"]
    return []


def ytdlp_hint(stderr: str) -> str:
    """Message clair pour les erreurs yt-dlp les plus courantes."""
    low = stderr.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube bloque les téléchargements depuis les serveurs (adresse de datacenter). Télécharge la vidéo "
                "sur ton ordinateur puis envoie-la au bot, ou utilise un autre site.")
    if "javascript" in low or "js runtime" in low or "challenge" in low:
        return "yt-dlp a besoin d'un moteur JavaScript (Deno ou Node.js) pour YouTube."
    if "unsupported url" in low:
        return "Ce site n'est pas pris en charge par yt-dlp."
    return stderr[-300:]


# ---------------------------------------------------------------------- accès à YouTube depuis un serveur
# YouTube refuse souvent les adresses de datacenter (« Sign in to confirm you're not a bot »). Le téléchargement
# est d'abord tenté en direct ; s'il est bloqué, StoryForge essaie des proxies gratuits fournis par l'API publique
# de Proxora (https://proxora.io/free-proxy-list/api), sans clé ni réglage.
TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # « Me at the zoo », 19 s
PROXORA_URL = "https://api.proxora.io/api/tools/free-proxies"
MAX_ATTEMPTS = 6  # proxies essayés au plus pour un même téléchargement
BAD_FOR = 3600  # un proxy en échec est mis de côté 1 h
CACHE_FOR = 600  # la liste Proxora est relue au plus toutes les 10 min (limite par IP)
NOT_RETRYABLE = ("unsupported url", "video unavailable", "private video", "file is larger", "is not a valid url",
                 "this video has been removed")

_proxora: dict = {"at": 0.0, "items": [], "error": ""}
_bad: dict[str, float] = {}  # proxy -> mis de côté jusqu'à
_good: list[str] = []  # proxies qui ont fonctionné, le plus récent en tête


def mask_proxy(value: str) -> str:
    return re.sub(r"//[^@/]+@", "//•••@", value or "")


async def ytdlp_options(settings: Settings) -> list[str]:
    """Options yt-dlp communes : moteur JavaScript pour YouTube."""
    return js_runtime_args()


def _rank(items: list[dict]) -> list[str]:
    """Adresses Proxora, les plus prometteuses pour YouTube d'abord : sorties résidentielles / mobiles,
    HTTPS fiable, non altérées, meilleur score ; une seule adresse par machine."""
    exit_rank = {"isp": 0, "mobile": 0, "hosting": 1}
    usable = [i for i in items if i.get("host") and i.get("port") and not i.get("tampered")
              and i.get("protocol") in ("http", "socks4", "socks5")]
    usable.sort(key=lambda i: (exit_rank.get(i.get("exit_type", ""), 2), not i.get("https"), -(i.get("score") or 0),
                               i.get("latency_ms") or 9999))
    out, hosts = [], set()
    for i in usable:
        if i["host"] in hosts:
            continue
        hosts.add(i["host"])
        out.append(f"{i['protocol']}://{i['host']}:{i['port']}")
    return out


async def proxora_proxies(*, force: bool = False) -> list[str]:
    """Proxies gratuits Proxora vérifiés récemment (mis en cache 10 min)."""
    import time

    import httpx

    now = time.time()
    if not force and _proxora["items"] and now - _proxora["at"] < CACHE_FOR:
        return list(_proxora["items"])
    items: list[dict] = []
    queries = [
        {"exit": "isp", "https": "true", "checked_within": 120, "sort": "score", "per_page": 50},
        {"exit": "mobile", "https": "true", "checked_within": 120, "sort": "score", "per_page": 50},
        {"https": "true", "checked_within": 60, "sort": "score", "per_page": 100},
    ]
    error = ""
    async with httpx.AsyncClient(timeout=20) as client:
        for q in queries:
            try:
                r = await client.get(PROXORA_URL, params={**q, "format": "json"})
                r.raise_for_status()
                items += r.json().get("items") or []
            except Exception as exc:
                error = str(exc)[:200]
    ranked = _rank(items)
    if ranked or not _proxora["items"]:
        _proxora.update(at=now, items=ranked, error="" if ranked else (error or "liste vide"))
    return list(_proxora["items"])


def proxora_status() -> dict:
    import time

    return {"count": len(_proxora["items"]), "age": time.time() - _proxora["at"] if _proxora["at"] else None,
            "error": _proxora["error"], "good": list(_good[:3]),
            "bad": sum(1 for t in _bad.values() if t > time.time())}


def _usable(proxies: list[str]) -> list[str]:
    import time

    now = time.time()
    ordered = [p for p in _good if p in proxies] + [p for p in proxies if p not in _good]
    return [p for p in ordered if _bad.get(p, 0) <= now]


def mark_proxy(url: str, ok: bool) -> None:
    import time

    if ok:
        _bad.pop(url, None)
        if url in _good:
            _good.remove(url)
        _good.insert(0, url)
        del _good[20:]
    else:
        _bad[url] = time.time() + BAD_FOR
        if url in _good:
            _good.remove(url)


class YtdlpResult:
    def __init__(self, returncode: int, out: bytes, err: bytes, proxy: str | None, attempts: int,
                 cancelled: bool = False):
        self.returncode, self.out, self.err, self.proxy, self.attempts = returncode, out, err, proxy, attempts
        self.cancelled = cancelled

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def error(self) -> str:
        return self.err.decode("utf-8", "replace")


PROGRESS_TAG = "SFPROG"
PROGRESS_TEMPLATE = (f"download:{PROGRESS_TAG} %(progress._percent_str)s|%(progress._speed_str)s|"
                     "%(progress._eta_str)s|%(progress._total_bytes_str)s%(progress._total_bytes_estimate_str)s")


def _kill_group(proc) -> None:
    import signal

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def parse_progress(line: str) -> dict | None:
    if PROGRESS_TAG not in line:
        return None
    parts = [p.strip() for p in line.split(PROGRESS_TAG, 1)[1].split("|")]
    parts += [""] * (4 - len(parts))
    try:
        pct = float(parts[0].rstrip("%"))
    except ValueError:
        pct = 0.0
    size = re.search(r"\d+(?:\.\d+)?\s*[KMGT]?i?B", parts[3])
    speed = parts[1] if re.search(r"\d", parts[1]) else ""  # « Unknown B/s », « N/A » au démarrage
    eta = parts[2] if re.search(r"\d", parts[2]) else ""
    return {"percent": pct, "speed": speed, "eta": eta, "size": size.group(0) if size else ""}


async def run_ytdlp(settings: Settings, args: list[str], *, timeout: float = 3 * 3600, on_progress=None,
                    cancel=None, stall: float = 120.0) -> YtdlpResult:
    """Lance yt-dlp ; avec une liste de proxies, passe au suivant tant que le téléchargement échoue.

    on_progress(dict) reçoit l'avancement (pourcentage, vitesse, temps restant, proxy essayé). Un téléchargement
    qui n'avance plus depuis `stall` secondes est coupé (proxy suivant), `cancel` (asyncio.Event) l'annule.
    """
    import asyncio
    import time

    exe = ytdlp_command()
    if not exe:
        return YtdlpResult(127, b"", "yt-dlp n'est pas installé : relance install.sh sur le serveur".encode(), None, 0)
    base = await ytdlp_options(settings)
    progress_args = ["--newline", "--progress", "--progress-template", PROGRESS_TEMPLATE] if on_progress else []
    result = YtdlpResult(1, b"", b"", None, 0)
    # Essai direct d'abord ; les proxies Proxora ne sont lus que si le site bloque le serveur.
    candidates: list[str | None] = [None]
    fetched = False
    attempt = 0
    while attempt < len(candidates):
        proxy = candidates[attempt]
        attempt += 1
        extra = ["--proxy", proxy, "--socket-timeout", "20"] if proxy else []
        if on_progress:
            await on_progress({"percent": 0.0, "speed": "", "eta": "", "size": "", "attempt": attempt,
                               "attempts": len(candidates), "proxy": proxy, "phase": "start"})
        # Groupe de processus à part : l'annulation coupe aussi les enfants de yt-dlp (ffmpeg…)
        proc = await asyncio.create_subprocess_exec(*exe, *base, *extra, *progress_args, *args,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                    start_new_session=True)
        out_lines: list[bytes] = []
        err_lines: list[bytes] = []
        state = {"activity": time.monotonic(), "percent": 0.0}

        async def pump(stream, sink: list[bytes]) -> None:
            while True:
                line = await stream.readline()
                if not line:
                    break
                info = parse_progress(line.decode("utf-8", "replace"))
                if info is None:
                    sink.append(line)
                    continue
                if info["percent"] != state["percent"]:
                    state["activity"] = time.monotonic()
                state["percent"] = info["percent"]
                if on_progress:
                    info.update(attempt=attempt, attempts=len(candidates), proxy=proxy, phase="download")
                    try:
                        await on_progress(info)
                    except Exception:
                        pass

        readers = asyncio.ensure_future(asyncio.gather(pump(proc.stdout, out_lines), pump(proc.stderr, err_lines)))
        started, reason = time.monotonic(), ""
        while not readers.done():
            await asyncio.wait({readers}, timeout=1)
            if readers.done():
                break
            now = time.monotonic()
            if cancel is not None and cancel.is_set():
                reason = "cancel"
            elif on_progress and state["percent"] < 99.9 and now - state["activity"] > stall:
                reason = "stall"  # plus aucune progression (proxy mort ou trop lent)
            elif now - started > timeout:
                reason = "timeout"
            if reason:
                _kill_group(proc)
                break
        if reason:  # les sorties se ferment avec le groupe ; on n'attend pas indéfiniment
            try:
                await asyncio.wait_for(asyncio.shield(readers), timeout=5)
            except asyncio.TimeoutError:
                readers.cancel()
        await asyncio.gather(readers, return_exceptions=True)
        code = await proc.wait()
        out, err = b"".join(out_lines), b"".join(err_lines)
        if reason == "cancel":
            return YtdlpResult(130, out, "téléchargement annulé".encode(), proxy, attempt, cancelled=True)
        if reason == "stall":
            err += f"\naucune progression depuis {int(stall)} s".encode()
        elif reason == "timeout":
            err += "\ndélai d'attente dépassé".encode()
        result = YtdlpResult(1 if reason else code, out, err, proxy, attempt)
        if result.ok:
            if proxy:
                mark_proxy(proxy, True)
            return result
        if proxy:
            mark_proxy(proxy, False)
        if any(x in result.error.lower() for x in NOT_RETRYABLE):
            break
        if not fetched:  # bloqué en direct : on passe par les proxies gratuits
            fetched = True
            candidates += _usable(await proxora_proxies())[:MAX_ATTEMPTS]
    return result
