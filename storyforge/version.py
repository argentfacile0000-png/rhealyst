"""Version déployée (écrite par deploy/install.sh et deploy/update.sh à côté du code)."""

from __future__ import annotations

import json
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[1] / ".version.json"


def deployed_version() -> dict | None:
    """{"sha", "message", "date"} si le projet a été installé par le script de déploiement."""
    try:
        data = json.loads(VERSION_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    sha = str(data.get("sha", ""))
    if len(sha) < 7:
        return None
    return {"sha": sha, "short": sha[:7], "message": str(data.get("message", "")), "date": str(data.get("date", ""))}


def auto_update_flag(data_dir: Path) -> Path:
    """Fichier dont la présence suspend les mises à jour automatiques."""
    return data_dir / "state" / "auto_update_disabled"
