"""Publication YouTube Shorts via YouTube Data API v3 (OAuth par chaîne)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from .base import PublisherError, PublishRequest, PublishResult

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def _flow(client_secrets_file: Path, redirect_uri: str, code_verifier: str | None = None):
    from google_auth_oauthlib.flow import Flow

    if not client_secrets_file.exists():
        raise PublisherError(
            f"Fichier OAuth Google introuvable ({client_secrets_file}). "
            "Télécharge le JSON 'client OAuth' depuis Google Cloud Console."
        )
    return Flow.from_client_secrets_file(
        str(client_secrets_file), scopes=SCOPES, redirect_uri=redirect_uri, code_verifier=code_verifier
    )


def build_auth_url(client_secrets_file: Path, redirect_uri: str, state: str) -> tuple[str, str | None]:
    """Retourne (url d'autorisation, code_verifier PKCE à conserver)."""
    flow = _flow(client_secrets_file, redirect_uri)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true", state=state)
    return url, flow.code_verifier


def _exchange_code_sync(client_secrets_file: Path, redirect_uri: str, code: str, code_verifier: str | None) -> dict:
    flow = _flow(client_secrets_file, redirect_uri, code_verifier=code_verifier)
    flow.fetch_token(code=code)
    return json.loads(flow.credentials.to_json())


async def exchange_code(client_secrets_file: Path, redirect_uri: str, code: str, code_verifier: str | None) -> dict:
    try:
        return await asyncio.to_thread(_exchange_code_sync, client_secrets_file, redirect_uri, code, code_verifier)
    except PublisherError:
        raise
    except Exception as exc:
        raise PublisherError(f"Échec de l'autorisation Google : {exc}") from exc


def _credentials(info: dict):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise PublisherError("Jeton YouTube expiré : relie le compte depuis le dashboard")
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise PublisherError(f"Accès YouTube révoqué ou expiré ({exc}) : relie le compte à nouveau") from exc
    return creds


def _service(creds):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _channel_title_sync(info: dict) -> tuple[str, dict]:
    creds = _credentials(info)
    resp = _service(creds).channels().list(part="snippet", mine=True).execute()
    items = resp.get("items") or []
    title = items[0]["snippet"]["title"] if items else "(aucune chaîne)"
    return title, json.loads(creds.to_json())


async def channel_title(info: dict) -> tuple[str, dict]:
    return await asyncio.to_thread(_channel_title_sync, info)


def _build_description(req: PublishRequest) -> str:
    tags = " ".join(f"#{t}" for t in req.tags[:8])
    desc = req.description.strip()
    return f"{desc}\n\n{tags} #shorts".strip()[:4900]


def _upload_sync(info: dict, req: PublishRequest) -> PublishResult:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    creds = _credentials(info)
    yt = _service(creds)
    title = req.title.strip() or "Storytime"
    if len(title) > 100:
        title = title[:97].rstrip() + "..."
    body: dict[str, Any] = {
        "snippet": {
            "title": title.replace("<", "").replace(">", ""),
            "description": _build_description(req).replace("<", "").replace(">", ""),
            "tags": [t[:30] for t in req.tags[:15]],
            "categoryId": "24",  # Entertainment
            "defaultLanguage": req.language,
            "defaultAudioLanguage": req.language,
        },
        "status": {
            "privacyStatus": req.privacy if req.privacy in ("public", "unlisted", "private") else "public",
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,  # voix générée par IA : déclaration obligatoire
        },
    }
    media = MediaFileUpload(req.video_path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=True)
    try:
        response = None
        retries = 0
        while response is None:
            try:
                _, response = request.next_chunk()
            except HttpError as exc:
                if exc.resp.status in (500, 502, 503, 504) and retries < 5:
                    retries += 1
                    continue
                raise
    except HttpError as exc:
        reason = ""
        try:
            reason = json.loads(exc.content.decode())["error"]["errors"][0]["reason"]
        except Exception:
            reason = str(exc)
        if reason in ("quotaExceeded", "uploadLimitExceeded"):
            raise PublisherError("Quota YouTube atteint pour aujourd'hui (réessaie demain)") from exc
        raise PublisherError(f"YouTube a refusé l'upload : {reason}", retryable=exc.resp.status >= 500) from exc

    vid = response.get("id", "")
    return PublishResult(
        status="published",
        remote_id=vid,
        url=f"https://youtube.com/shorts/{vid}" if vid else "",
        credentials=json.loads(creds.to_json()),
    )


async def upload(info: dict, req: PublishRequest) -> PublishResult:
    try:
        return await asyncio.to_thread(_upload_sync, info, req)
    except PublisherError:
        raise
    except Exception as exc:
        raise PublisherError(f"Erreur YouTube : {exc}", retryable=True) from exc
