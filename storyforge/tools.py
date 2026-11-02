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
# YouTube refuse souvent les adresses de datacenter (« Sign in to confirm you're not a bot »). Deux réglages :
# les cookies d'une session YouTube connectée (ce que YouTube demande), ou un proxy.
COOKIES_NAME = "ytdlp_cookies.txt"
PROXY_KEY = "ytdlp_proxy"
PROXY_RE = re.compile(r"(https?|socks5h?|socks4a?)://\S+")
TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # « Me at the zoo », 19 s


def cookies_path(settings: Settings) -> Path:
    return Path(settings.google_client_secrets_file).parent / COOKIES_NAME


def parse_cookies(raw: bytes) -> tuple[str, int]:
    """Valide un cookies.txt (format Netscape). Retourne (contenu, nombre de cookies YouTube/Google)."""
    if len(raw) > 1_000_000:
        raise ValueError("Fichier trop gros pour un cookies.txt")
    text = raw.decode("utf-8-sig", "replace").replace("\r\n", "\n")
    rows = [line.split("\t") for line in text.split("\n")
            if line.strip() and (not line.startswith("#") or line.startswith("#HttpOnly_"))]
    if not rows or any(len(r) < 7 for r in rows):
        raise ValueError("Ce n'est pas un cookies.txt au format Netscape : exporte-le avec une extension "
                         "« cookies.txt » depuis youtube.com")
    count = sum(1 for r in rows if r[0].replace("#HttpOnly_", "").lstrip(".").endswith(("youtube.com", "google.com")))
    if not count:
        raise ValueError("Aucun cookie youtube.com dans ce fichier : exporte-le depuis un onglet youtube.com connecté")
    if not text.startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        text = "# Netscape HTTP Cookie File\n" + text
    return text, count


def save_cookies(settings: Settings, raw: bytes) -> int:
    text, count = parse_cookies(raw)
    path = cookies_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    tmp.replace(path)
    return count


def check_proxy(value: str) -> str:
    value = value.strip()
    if not PROXY_RE.fullmatch(value):
        raise ValueError("Adresse de proxy invalide. Exemples : http://utilisateur:motdepasse@hote:port, "
                         "socks5://hote:1080")
    return value


def mask_proxy(value: str) -> str:
    return re.sub(r"//[^@/]+@", "//•••@", value)


async def ytdlp_options(settings: Settings) -> list[str]:
    """Options yt-dlp communes : moteur JavaScript, cookies YouTube et proxy s'ils sont configurés."""
    from . import db

    args = js_runtime_args()
    cookies = cookies_path(settings)
    if cookies.is_file():
        args += ["--cookies", str(cookies)]
    proxy = await db.kv_get(PROXY_KEY, "") or settings.ytdlp_proxy
    if proxy:
        args += ["--proxy", proxy]
    return args


# ---------------------------------------------------------------------- liste de proxies (rotation)
PROXIES_KEY = "ytdlp_proxies"
_LOCKS: dict[int, object] = {}  # verrou des modifications de la liste, un par boucle asyncio


def _lock():
    import asyncio

    key = id(asyncio.get_running_loop())
    lock = _LOCKS.get(key)
    if lock is None:
        _LOCKS.clear()
        lock = _LOCKS[key] = asyncio.Lock()
    return lock
MAX_PROXIES = 1000
MAX_ATTEMPTS = 6  # proxies essayés au plus pour un même téléchargement
BAD_FOR = 3600  # un proxy en échec est mis de côté 1 h
CHECK_URL = "https://www.youtube.com/generate_204"
HOST_PORT = re.compile(r"[A-Za-z0-9.\-]+:\d{2,5}")
NOT_RETRYABLE = ("unsupported url", "video unavailable", "private video", "file is larger", "is not a valid url",
                 "this video has been removed")


def normalize_proxy(token: str, default_scheme: str = "http") -> str | None:
    """« ip:port », « user:pass@ip:port », « ip:port:user:pass » ou une adresse complète → adresse de proxy."""
    token = token.strip()
    if "://" in token:
        return token if PROXY_RE.fullmatch(token) else None
    if "@" in token:
        creds, host = token.rsplit("@", 1)
        return f"{default_scheme}://{creds}@{host}" if HOST_PORT.fullmatch(host) and ":" in creds else None
    if HOST_PORT.fullmatch(token):
        return f"{default_scheme}://{token}"
    parts = token.split(":")
    if len(parts) == 4 and HOST_PORT.fullmatch(f"{parts[0]}:{parts[1]}"):
        return f"{default_scheme}://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return None


def parse_proxy_list(text: str, filename: str = "") -> list[str]:
    """Une adresse par ligne. Le type par défaut vient du nom du fichier (socks5.txt, socks4.txt, sinon http)."""
    name = filename.lower()
    scheme = "socks5" if "socks5" in name else "socks4" if "socks4" in name else "http"
    out: list[str] = []
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line or line.startswith(("#", "//")):
            continue
        url = normalize_proxy(line.split()[0], scheme)
        if url and url not in out:
            out.append(url)
        if len(out) >= MAX_PROXIES:
            break
    return out


async def proxy_state() -> dict:
    from . import db

    data = await db.kv_get(PROXIES_KEY, {}) or {}
    return {"list": list(data.get("list") or []), "ok": list(data.get("ok") or []),
            "bad": dict(data.get("bad") or {}), "tested_at": data.get("tested_at")}


async def _save_state(state: dict) -> None:
    from . import db

    await db.kv_set(PROXIES_KEY, state)


async def set_proxy_list(urls: list[str]) -> None:
    async with _lock():
        await _save_state({"list": urls, "ok": [], "bad": {}, "tested_at": None})


async def proxy_rotation() -> list[str]:
    """Ordre d'essai : ceux qui ont marché d'abord, puis les autres, sans ceux mis de côté récemment."""
    import time

    st = await proxy_state()
    now = time.time()
    bad = {u for u, until in st["bad"].items() if until > now}
    ordered = [u for u in st["ok"] if u in st["list"]] + [u for u in st["list"] if u not in st["ok"]]
    return [u for u in ordered if u not in bad]


async def mark_proxy(url: str, ok: bool) -> None:
    import time

    async with _lock():
        st = await proxy_state()
        if url not in st["list"]:
            return
        if ok:
            st["ok"] = [url] + [u for u in st["ok"] if u != url]
            st["bad"].pop(url, None)
        else:
            st["ok"] = [u for u in st["ok"] if u != url]
            st["bad"][url] = time.time() + BAD_FOR
        await _save_state(st)


async def check_proxy_reaches_youtube(url: str, timeout: float = 10.0) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(proxy=url, timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(CHECK_URL)
        return resp.status_code in (200, 204)
    except Exception:
        return False


async def test_proxy_list(on_progress=None, concurrency: int = 64, per_proxy: float = 15.0,
                          deadline: float = 300.0) -> tuple[int, int]:
    """Teste toute la liste (YouTube joignable ?) et range les proxies fonctionnels en tête. Retourne (ok, total).

    Certains proxies gratuits acceptent la connexion puis ne répondent jamais : chaque essai est coupé après
    `per_proxy` secondes et le test entier après `deadline` secondes (les proxies restés muets comptent en échec).
    """
    import asyncio
    import time

    st = await proxy_state()
    urls = st["list"]
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, bool] = {}
    done = 0

    async def one(u: str) -> None:
        nonlocal done
        async with sem:
            try:
                results[u] = await asyncio.wait_for(check_proxy_reaches_youtube(u, timeout=per_proxy - 5),
                                                    timeout=per_proxy)
            except (asyncio.TimeoutError, Exception):
                results[u] = False
        done += 1
        if on_progress and (done % 50 == 0 or done == len(urls)):
            try:
                await on_progress(done, len(urls))
            except Exception:
                pass

    tasks = [asyncio.create_task(one(u)) for u in urls]
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=deadline)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    for u in urls:  # coupés par la limite de temps : en échec
        results.setdefault(u, False)
    ok = [u for u in urls if results.get(u)]
    # La liste a pu changer pendant le test (remplacée, retirée) : on ne met à jour que ce qui est encore dedans.
    now = time.time()
    async with _lock():
        current = await proxy_state()
        keep = current["list"]
        bad = {u: t for u, t in current["bad"].items() if u in keep and u not in results}
        bad.update({u: now + BAD_FOR for u in keep if u in results and not results[u]})
        await _save_state({
            "list": keep,
            "ok": [u for u in ok if u in keep] + [u for u in current["ok"] if u in keep and u not in results],
            "bad": bad,
            "tested_at": int(now) if keep else None,
        })
    return len(ok), len(urls)


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

    from . import db

    exe = ytdlp_command()
    if not exe:
        return YtdlpResult(127, b"", "yt-dlp n'est pas installé : relance install.sh sur le serveur".encode(), None, 0)
    base = await ytdlp_options(settings)
    single = await db.kv_get(PROXY_KEY, "") or settings.ytdlp_proxy
    candidates: list[str | None] = [None]
    if not single:
        rotation = await proxy_rotation()
        if rotation:
            candidates = list(rotation[:MAX_ATTEMPTS])
    progress_args = ["--newline", "--progress", "--progress-template", PROGRESS_TEMPLATE] if on_progress else []
    result = YtdlpResult(1, b"", b"", None, 0)
    for attempt, proxy in enumerate(candidates, start=1):
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
                await mark_proxy(proxy, True)
            return result
        if proxy:
            await mark_proxy(proxy, False)
        if any(x in result.error.lower() for x in NOT_RETRYABLE):
            break
    return result
