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
