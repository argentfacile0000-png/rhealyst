"""Publication de Reels via l'API Instagram (connexion Instagram, compte professionnel).

Avec la connexion Instagram, l'API ne reçoit pas le fichier : elle télécharge la vidéo
depuis une adresse publique en https. StoryForge sert donc chaque vidéo à publier via
une adresse signée et temporaire (nécessite PUBLIC_URL).

Étapes : conteneur REELS (video_url) → attente du traitement → media_publish → lien.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse

import httpx

from .base import PublisherError, PublishRequest, PublishResult

log = logging.getLogger(__name__)

AUTH_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"
SCOPES = "instagram_business_basic,instagram_business_content_publish"
DEFAULT_VERSION = "v26.0"


def build_auth_url(app_id: str, redirect_uri: str, state: str) -> str:
    if not app_id or not redirect_uri:
        raise PublisherError("INSTAGRAM_APP_ID / adresse de retour manquants")
    params = {"client_id": app_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPES, "state": state}
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def _check(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise PublisherError(f"Instagram {resp.status_code} : réponse illisible", retryable=resp.status_code >= 500)
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error") or {}
        if isinstance(err, str):
            msg = data.get("error_message") or err
            code = 0
        else:
            msg = err.get("error_user_msg") or err.get("message") or str(err)
            code = err.get("code", 0)
        if code == 190:
            msg = "accès expiré ou révoqué, reconnecte le compte"
        elif code in (4, 17, 32, 613) or resp.status_code == 429:
            msg = "limite de publications Instagram atteinte, réessaie plus tard"
        raise PublisherError(f"Instagram : {msg}", retryable=resp.status_code >= 500)
    return data


async def exchange_code(app_id: str, app_secret: str, redirect_uri: str, code: str, *, version: str = DEFAULT_VERSION) -> dict:
    """Code d'autorisation → jeton longue durée (60 jours)."""
    code = code.split("#")[0]  # Instagram ajoute parfois « #_ » à la fin du code
    async with httpx.AsyncClient(timeout=30) as client:
        short = _check(await client.post(TOKEN_URL, data={
            "client_id": app_id, "client_secret": app_secret, "grant_type": "authorization_code",
            "redirect_uri": redirect_uri, "code": code,
        }))
        if "data" in short and isinstance(short["data"], list) and short["data"]:
            short = short["data"][0]
        long_lived = _check(await client.get(f"{GRAPH}/access_token", params={
            "grant_type": "ig_exchange_token", "client_secret": app_secret, "access_token": short["access_token"],
        }))
    now = int(time.time())
    return {
        "access_token": long_lived["access_token"],
        "user_id": str(short.get("user_id", "")),
        "obtained_at": now,
        "expires_at": now + int(long_lived.get("expires_in", 5184000)),
    }


async def refresh_if_needed(creds: dict) -> tuple[dict, bool]:
    """Prolonge le jeton (60 jours) quand il a plus de 24 h et expire dans moins de 20 jours."""
    now = time.time()
    if not creds.get("access_token"):
        raise PublisherError("Compte Instagram non connecté")
    if creds.get("expires_at", 0) and creds["expires_at"] < now:
        raise PublisherError("Accès Instagram expiré : reconnecte le compte")
    if creds.get("expires_at", 0) - now > 20 * 86400 or now - creds.get("obtained_at", 0) < 86400:
        return creds, False
    async with httpx.AsyncClient(timeout=30) as client:
        data = _check(await client.get(f"{GRAPH}/refresh_access_token", params={
            "grant_type": "ig_refresh_token", "access_token": creds["access_token"],
        }))
    new = dict(creds, access_token=data["access_token"], obtained_at=int(now),
               expires_at=int(now) + int(data.get("expires_in", 5184000)))
    return new, True


async def user_info(access_token: str, *, version: str = DEFAULT_VERSION) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        return _check(await client.get(f"{GRAPH}/{version}/me", params={
            "fields": "user_id,username", "access_token": access_token,
        }))


async def publish(creds: dict, req: PublishRequest, *, version: str = DEFAULT_VERSION,
                  poll_interval: float = 10.0, timeout: float = 600.0) -> PublishResult:
    if not req.video_url.startswith("https://"):
        raise PublisherError("Instagram a besoin d'une adresse publique en https pour récupérer la vidéo "
                             "(configure PUBLIC_URL et WEBAPP_ENABLED)")
    token = creds["access_token"]
    user = creds.get("user_id") or "me"
    base = f"{GRAPH}/{version}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(60)) as client:
        container = _check(await client.post(f"{base}/{user}/media", data={
            "media_type": "REELS",
            "video_url": req.video_url,
            "caption": req.caption[:2200],
            "share_to_feed": "true",
            "thumb_offset": "1500",
            "access_token": token,
        }))
        cid = container.get("id")
        if not cid:
            raise PublisherError("Instagram n'a pas créé le conteneur de la vidéo")

        # Instagram télécharge et traite la vidéo de son côté
        deadline = time.monotonic() + timeout
        status = "IN_PROGRESS"
        info: dict = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            info = _check(await client.get(f"{base}/{cid}", params={"fields": "status_code,status", "access_token": token}))
            status = info.get("status_code", status)
            if status in ("FINISHED", "ERROR", "EXPIRED", "PUBLISHED"):
                break
        if status in ("ERROR", "EXPIRED"):
            raise PublisherError(f"Instagram a refusé la vidéo ({info.get('status') or status})")
        if status != "FINISHED" and status != "PUBLISHED":
            raise PublisherError("Instagram traite encore la vidéo : délai dépassé, réessaie plus tard", retryable=True)

        published = _check(await client.post(f"{base}/{user}/media_publish", data={"creation_id": cid, "access_token": token}))
        media_id = str(published.get("id", ""))
        url = ""
        if media_id:
            try:
                url = _check(await client.get(f"{base}/{media_id}", params={"fields": "permalink", "access_token": token})).get("permalink", "")
            except PublisherError:
                log.info("Lien du Reel indisponible pour l'instant")
    return PublishResult(status="published", remote_id=media_id, url=url)
