"""Publication TikTok via la Content Posting API officielle (OAuth par compte).

Deux modes :
- direct : publication directe (scope video.publish). Tant que l'app TikTok n'est pas
  auditée, TikTok n'autorise que la visibilité SELF_ONLY (privé).
- draft  : envoi dans la boîte de réception TikTok (scope video.upload) ; tu finalises
  la publication dans l'app (pratique pour ajouter un son tendance).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import urllib.parse
from typing import Any

import httpx

from .base import PublisherError, PublishRequest, PublishResult

log = logging.getLogger(__name__)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
# Brouillons : l'app n'a besoin que de Login Kit + « Upload » (le plus simple à faire valider par TikTok).
SCOPES_DRAFT = "user.info.basic,video.upload"
# Publication directe : ajoute video.publish (option « Direct Post » de la Content Posting API).
SCOPES_DIRECT = "user.info.basic,video.publish,video.upload"

MIN_CHUNK = 5 * 1024 * 1024
MAX_SINGLE = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024


def build_auth_url(client_key: str, redirect_uri: str, state: str, *, direct: bool = False) -> str:
    if not client_key or not redirect_uri:
        raise PublisherError("App TikTok non configurée (Réglages, Plateformes, TikTok)")
    params = {
        "client_key": client_key,
        "scope": SCOPES_DIRECT if direct else SCOPES_DRAFT,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def _with_expiry(data: dict) -> dict:
    now = int(time.time())
    out = dict(data)
    out["expires_at"] = now + int(data.get("expires_in", 86400))
    out["refresh_expires_at"] = now + int(data.get("refresh_expires_in", 31536000))
    return out


async def _token_request(form: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{API}/v2/oauth/token/",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
        )
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400 or "access_token" not in data:
        msg = data.get("error_description") or data.get("error") or resp.text[:200]
        raise PublisherError(f"OAuth TikTok refusé : {msg}")
    return _with_expiry(data)


async def check_client(client_key: str, client_secret: str) -> bool | None:
    """Vérifie les identifiants de l'app (jeton « client_credentials »).

    Vrai si TikTok les accepte, None si TikTok est injoignable ; lève PublisherError s'ils sont refusés.
    """
    try:
        await _token_request({"client_key": client_key, "client_secret": client_secret,
                              "grant_type": "client_credentials"})
    except httpx.HTTPError:
        return None
    return True


async def exchange_code(client_key: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    return await _token_request(
        {
            "client_key": client_key,
            "client_secret": client_secret,
            "code": urllib.parse.unquote(code),
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    )


async def refresh_if_needed(client_key: str, client_secret: str, creds: dict) -> tuple[dict, bool]:
    """Rafraîchit le jeton d'accès (valable 24 h) si besoin. Retourne (creds, modifié?)."""
    if creds.get("expires_at", 0) - time.time() > 600:
        return creds, False
    if not creds.get("refresh_token"):
        raise PublisherError("Jeton TikTok expiré : relie le compte depuis le dashboard")
    new = await _token_request(
        {
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
        }
    )
    merged = {**creds, **new}
    return merged, True


def _check(resp: httpx.Response) -> dict:
    try:
        payload = resp.json()
    except ValueError:
        raise PublisherError(f"TikTok {resp.status_code}: réponse illisible", retryable=resp.status_code >= 500)
    err = payload.get("error") or {}
    code = err.get("code", "ok")
    if resp.status_code >= 400 or code != "ok":
        hints = {
            "spam_risk_too_many_posts": "trop de publications aujourd'hui sur ce compte",
            "spam_risk_user_banned_from_posting": "compte bloqué pour la publication",
            "unaudited_client_can_only_post_to_private_accounts": "app pas encore auditée par TikTok : seule la "
            "visibilité « Moi seul » est possible pour l'instant (ou le mode brouillon)",
            "privacy_level_option_mismatch": "niveau de visibilité non autorisé pour ce compte",
            "access_token_invalid": "jeton invalide, relie le compte",
            "scope_not_authorized": "autorisation manquante (scopes), relie le compte",
            "rate_limit_exceeded": "limite de requêtes atteinte, réessaie plus tard",
        }
        detail = hints.get(code, err.get("message") or code)
        raise PublisherError(f"TikTok : {detail}", retryable=code == "rate_limit_exceeded" or resp.status_code >= 500)
    return payload.get("data") or {}


async def user_info(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API}/v2/user/info/",
            params={"fields": "open_id,display_name,avatar_url"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    return _check(resp).get("user", {})


async def creator_info(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.post(f"{API}/v2/post/publish/creator_info/query/", headers=headers, json={})
    return _check(resp)


async def fetch_creator_info(access_token: str) -> dict:
    """Pseudo, avatar, visibilités autorisées, interactions désactivées et durée maximale du créateur."""
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=UTF-8"}
        return await creator_info(client, headers)


PRIVACY_LABELS = {
    "PUBLIC_TO_EVERYONE": "Tout le monde",
    "FOLLOWER_OF_CREATOR": "Abonnés",
    "MUTUAL_FOLLOW_FRIENDS": "Amis",
    "SELF_ONLY": "Moi seul",
}


def check_post_settings(settings: dict, creator: dict) -> list[str]:
    """Erreurs bloquantes des réglages choisis (règles de publication de TikTok)."""
    errors = []
    if settings.get("privacy") not in (creator.get("privacy_level_options") or []):
        errors.append("Choisis la visibilité des vidéos.")
    if settings.get("disclose") and not (settings.get("brand_organic") or settings.get("brand_content")):
        errors.append("Contenu commercial : indique s'il s'agit de ta marque, de contenu de marque, ou des deux.")
    if settings.get("brand_content") and settings.get("privacy") == "SELF_ONLY":
        errors.append("Un contenu de marque ne peut pas être privé : choisis une autre visibilité.")
    return errors


def post_info_from(settings: dict | None, creator: dict, caption: str) -> dict[str, Any]:
    """Paramètres de publication directe, à partir des réglages choisis par le créateur."""
    if not settings or not settings.get("consented_at"):
        raise PublisherError("Choisis d'abord tes réglages de publication TikTok : page Comptes de la chaîne, "
                             "TikTok, Réglages de publication")
    errors = check_post_settings(settings, creator)
    if errors:
        raise PublisherError("Réglages de publication TikTok à revoir : " + " ".join(errors))
    disclose = bool(settings.get("disclose"))
    return {
        "title": caption[:2200],
        "privacy_level": settings["privacy"],
        "disable_comment": bool(creator.get("comment_disabled")) or not settings.get("comment"),
        "disable_duet": bool(creator.get("duet_disabled")) or not settings.get("duet"),
        "disable_stitch": bool(creator.get("stitch_disabled")) or not settings.get("stitch"),
        "brand_content_toggle": disclose and bool(settings.get("brand_content")),
        "brand_organic_toggle": disclose and bool(settings.get("brand_organic")),
        "video_cover_timestamp_ms": 1500,
        "is_aigc": True,  # contenu généré par IA (voix) : étiquette obligatoire
    }


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) selon les règles de l'API TikTok."""
    if size <= MAX_SINGLE:
        return size, 1
    count = max(1, math.floor(size / CHUNK))
    return CHUNK, count


def effective_mode(creds: dict, mode: str) -> str:
    """Brouillon si le compte n'a pas autorisé la publication directe (scope video.publish absent)."""
    granted = {s.strip() for s in str(creds.get("scope") or "").split(",") if s.strip()}
    if mode != "draft" and granted and "video.publish" not in granted:
        return "draft"
    return mode


async def publish(creds: dict, req: PublishRequest, *, username: str = "") -> PublishResult:
    req.mode = effective_mode(creds, req.mode)
    if req.mode != "draft" and not (req.tiktok or {}).get("consented_at"):
        # Le créateur n'a pas encore choisi ses réglages de publication : jamais de publication directe sans eux.
        log.warning("Réglages de publication TikTok non choisis : envoi en brouillon")
        req.mode = "draft"
    token = creds.get("access_token")
    if not token:
        raise PublisherError("Compte TikTok non lié")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    size = os.path.getsize(req.video_path)
    chunk_size, count = chunk_plan(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": count}

    async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=300, write=300)) as client:
        if req.mode == "draft":
            init = _check(await client.post(f"{API}/v2/post/publish/inbox/video/init/", headers=headers,
                                            json={"source_info": source}))
        else:
            info = await creator_info(client, headers)
            max_seconds = info.get("max_video_post_duration_sec")
            if max_seconds and req.duration and req.duration > max_seconds:
                raise PublisherError(f"Vidéo trop longue pour ce compte TikTok ({int(req.duration)} s, "
                                     f"maximum {max_seconds} s) : découpe-la en parties")
            post_info = post_info_from(req.tiktok, info, req.caption)
            init = _check(await client.post(f"{API}/v2/post/publish/video/init/", headers=headers,
                                            json={"post_info": post_info, "source_info": source}))

        publish_id = init.get("publish_id", "")
        upload_url = init.get("upload_url")
        if not upload_url:
            raise PublisherError("TikTok n'a pas fourni d'URL d'upload")

        with open(req.video_path, "rb") as fh:
            for i in range(count):
                start = i * chunk_size
                end = size - 1 if i == count - 1 else start + chunk_size - 1
                fh.seek(start)
                data = fh.read(end - start + 1)
                resp = await client.put(
                    upload_url,
                    content=data,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                    },
                )
                if resp.status_code not in (200, 201, 206):
                    raise PublisherError(f"Upload TikTok interrompu (HTTP {resp.status_code})", retryable=True)

        # Suivi du traitement côté TikTok
        deadline = time.monotonic() + 600
        status = "PROCESSING_UPLOAD"
        data: dict = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(6)
            data = _check(await client.post(f"{API}/v2/post/publish/status/fetch/", headers=headers,
                                            json={"publish_id": publish_id}))
            status = data.get("status", status)
            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"):
                break

    if status == "FAILED":
        raise PublisherError(f"TikTok a rejeté la vidéo : {data.get('fail_reason', 'raison inconnue')}")
    if status == "SEND_TO_USER_INBOX":
        return PublishResult(status="draft", remote_id=publish_id)
    if status == "PUBLISH_COMPLETE":
        ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
        post_id = str(ids[0]) if ids else ""
        url = f"https://www.tiktok.com/@{username}/video/{post_id}" if post_id and username else ""
        return PublishResult(status="published", remote_id=post_id or publish_id, url=url)
    return PublishResult(status="processing", remote_id=publish_id)
