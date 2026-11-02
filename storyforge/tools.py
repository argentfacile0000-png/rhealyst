"""Outils externes : yt-dlp (téléchargement de fonds et de musiques par lien)."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path


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
