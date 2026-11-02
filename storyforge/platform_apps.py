"""Identifiants des « apps développeur » (Google, TikTok, Instagram) configurables depuis le bot.

Sans eux, impossible de connecter un compte : chaque plateforme exige une app enregistrée à ton nom.
Ils restent sur le serveur (base de données ou dossier secrets/) et priment sur ceux du .env.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from . import db

if TYPE_CHECKING:
    from .config import Settings

log = logging.getLogger(__name__)

KV_KEY = "platform_apps"
KEYS = {
    "tiktok": ("tiktok_client_key", "tiktok_client_secret"),
    "instagram": ("instagram_app_id", "instagram_app_secret"),
}
PATTERNS = {
    "tiktok_client_key": (r"[A-Za-z0-9]{8,64}", "Client key TikTok invalide (lettres et chiffres, sans espace)"),
    "tiktok_client_secret": (r"[A-Za-z0-9_\-]{16,128}", "Client secret TikTok invalide"),
    "instagram_app_id": (r"\d{6,25}", "L'ID de l'app Instagram ne contient que des chiffres"),
    "instagram_app_secret": (r"[A-Za-z0-9]{16,64}", "Clé secrète Instagram invalide (32 caractères en général)"),
}


def check_value(key: str, value: str) -> str:
    value = value.strip()
    pattern, message = PATTERNS[key]
    if not re.fullmatch(pattern, value):
        raise ValueError(message)
    return value


def parse_google_client(raw: bytes) -> tuple[str, dict]:
    """Valide un fichier client OAuth Google. Retourne (type, contenu) : type « web » ou « installed »."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("Ce fichier n'est pas un JSON valide : télécharge-le depuis Google Cloud, Identifiants")
    if not isinstance(data, dict):
        raise ValueError("Fichier inattendu : ce n'est pas un client OAuth Google")
    kind = "web" if "web" in data else "installed" if "installed" in data else ""
    if not kind:
        raise ValueError("Ce JSON n'est pas un client OAuth (il faut un « ID client OAuth », pas une clé de compte de service)")
    client = data[kind]
    if not str(client.get("client_id", "")).endswith(".apps.googleusercontent.com") or not client.get("client_secret"):
        raise ValueError("Client OAuth incomplet : client_id ou client_secret manquant")
    return kind, data


class PlatformApps:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._env = {k: getattr(settings, k) for keys in KEYS.values() for k in keys}
        self._stored: dict[str, str] = {}

    async def load(self) -> None:
        self._stored = {k: v for k, v in (await db.kv_get(KV_KEY, {}) or {}).items() if k in self._env and v}
        for k, v in self._stored.items():
            setattr(self.settings, k, v)

    def source(self, platform: str) -> str | None:
        """« bot », « .env » ou None selon l'origine des identifiants de la plateforme."""
        if platform == "youtube":
            return "fichier" if self.settings.google_client_secrets_file.exists() else None
        keys = KEYS[platform]
        if all(self._stored.get(k) for k in keys):
            return "bot"
        if all(getattr(self.settings, k) for k in keys):
            return ".env"
        return None

    async def set(self, platform: str, values: dict[str, str]) -> None:
        clean = {k: check_value(k, v) for k, v in values.items() if k in KEYS[platform]}
        self._stored.update(clean)
        await db.kv_set(KV_KEY, self._stored)
        for k, v in clean.items():
            setattr(self.settings, k, v)
        await db.log_event(f"Identifiants de l'app {platform.capitalize()} enregistrés depuis le bot", "success")

    async def clear(self, platform: str) -> None:
        if platform == "youtube":
            self.settings.google_client_secrets_file.unlink(missing_ok=True)
        else:
            for k in KEYS[platform]:
                self._stored.pop(k, None)
                setattr(self.settings, k, self._env[k])
            await db.kv_set(KV_KEY, self._stored)
        await db.log_event(f"Identifiants de l'app {platform.capitalize()} retirés", "info")

    # ------------------------------------------------------------------ Google
    def google_client(self) -> tuple[str, dict] | None:
        path = self.settings.google_client_secrets_file
        try:
            return parse_google_client(path.read_bytes())
        except (OSError, ValueError):
            return None

    async def save_google(self, raw: bytes, redirect_uri: str) -> list[str]:
        """Enregistre le client OAuth Google. Retourne des avertissements éventuels (sans bloquer)."""
        kind, data = parse_google_client(raw)
        path: Path = self.settings.google_client_secrets_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        tmp.replace(path)
        warnings = []
        if kind == "installed":
            warnings.append("C'est un client « Application de bureau » : la connexion se fera en collant l'adresse "
                            "de retour. Pour une connexion en un clic, crée plutôt un client « Application Web ».")
        elif redirect_uri and redirect_uri not in (data["web"].get("redirect_uris") or []):
            warnings.append(f"Vérifie que l'URI de redirection {redirect_uri} est bien autorisée dans Google Cloud "
                            "(Clients, ton client Web, URI de redirection autorisés).")
        await db.log_event("Client OAuth Google enregistré depuis le bot", "success")
        return warnings


# ---------------------------------------------------------------------- vérification du site
# TikTok (et d'autres) vérifient que tu contrôles l'adresse du serveur. Avec un domaine sslip.io, impossible
# d'ajouter un enregistrement DNS : on choisit la méthode « URL prefix » et le serveur publie le fichier fourni.
VERIFY_KEY = "site_verifications"
VERIFY_NAME = re.compile(r"[A-Za-z0-9_\-]{3,100}\.txt")
TIKTOK_LINE = re.compile(r"tiktok-developers-site-verification=([A-Za-z0-9_\-]{8,128})")
MAX_VERIFICATIONS = 10


def parse_verification(filename: str | None, content: str) -> tuple[str, str]:
    """(nom du fichier publié, contenu) à partir d'un fichier envoyé ou de la ligne collée."""
    content = content.strip()
    if not content or len(content) > 512 or not re.fullmatch(r"[A-Za-z0-9_\-=.:]+", content):
        raise ValueError("Contenu inattendu : un fichier de vérification tient sur une ligne, "
                         "par exemple tiktok-developers-site-verification=…")
    if filename:
        name = Path(filename).name
        if not VERIFY_NAME.fullmatch(name):
            raise ValueError("Nom de fichier inattendu : envoie le fichier .txt téléchargé tel quel")
        return name, content
    m = TIKTOK_LINE.fullmatch(content)
    if not m:
        raise ValueError("Colle la ligne tiktok-developers-site-verification=…, ou envoie le fichier .txt")
    return f"tiktok{m.group(1)}.txt", content


async def verifications() -> dict[str, str]:
    data = await db.kv_get(VERIFY_KEY, {}) or {}
    return {k: v for k, v in data.items() if VERIFY_NAME.fullmatch(k) and isinstance(v, str)}


async def add_verification(name: str, content: str) -> None:
    data = await verifications()
    data.pop(name, None)
    data[name] = content
    while len(data) > MAX_VERIFICATIONS:
        data.pop(next(iter(data)))
    await db.kv_set(VERIFY_KEY, data)
    await db.log_event(f"Fichier de vérification publié : /{name}", "success")


async def clear_verifications() -> None:
    await db.kv_set(VERIFY_KEY, {})
    await db.log_event("Fichiers de vérification retirés", "info")
