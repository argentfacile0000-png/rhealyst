"""Validation des données d'initialisation d'une Telegram Mini App (initData)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse


def _check(pairs: dict[str, str], bot_token: str, received_hash: str) -> bool:
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, received_hash)


# Durée de validité d'une session de Mini App : au-delà, il faut rouvrir le dashboard depuis le bot.
INIT_DATA_MAX_AGE = 6 * 3600


def validate_init_data(init_data: str, bot_token: str, max_age: int = INIT_DATA_MAX_AGE) -> dict | None:
    """Retourne l'utilisateur Telegram si la signature est valide, sinon None."""
    if not init_data or not bot_token:
        return None
    pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received:
        return None
    ok = _check(pairs, bot_token, received)
    if not ok and "signature" in pairs:  # compatibilité selon les versions de clients
        ok = _check({k: v for k, v in pairs.items() if k != "signature"}, bot_token, received)
    if not ok:
        return None
    try:
        age = time.time() - int(pairs.get("auth_date", "0"))
        if age > max_age or age < -300:
            return None
        return json.loads(pairs.get("user", "{}"))
    except (ValueError, json.JSONDecodeError):
        return None
