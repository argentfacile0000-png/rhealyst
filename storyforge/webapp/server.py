"""Serveur web : Telegram Mini App (dashboard visuel) + retours OAuth."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select

from .. import db
from ..models import Video, VideoStatus
from ..stats import overview
from .auth import validate_init_data

if TYPE_CHECKING:
    from ..core import Services

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def _page(title: str, body: str, ok: bool = True) -> HTMLResponse:
    color = "#22c55e" if ok else "#ef4444"
    return HTMLResponse(f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0b14;color:#f4f4f8;
font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}.c{{max-width:420px;padding:32px;border-radius:24px;
background:linear-gradient(160deg,#1a1a2e,#12121c);border:1px solid #2a2a40;text-align:center}}
h1{{font-size:22px;color:{color}}}p{{color:#a0a0b8;line-height:1.5}}</style></head>
<body><div class="c"><h1>{html.escape(title)}</h1><p>{body}</p></div></body></html>""")


def create_app(svc: Services) -> FastAPI:
    app = FastAPI(title="StoryForge", docs_url=None, redoc_url=None, openapi_url=None)
    settings = svc.settings

    def user(request: Request) -> dict:
        init = request.headers.get("X-Telegram-Init-Data") or request.query_params.get("tma", "")
        u = validate_init_data(init, settings.telegram_bot_token)
        if not u or int(u.get("id", 0)) not in settings.admin_ids:
            raise HTTPException(status_code=401, detail="Non autorisé")
        return u

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse("/app")

    @app.get("/healthz")
    async def health() -> dict:
        return {"ok": True, "paused": await svc.is_paused()}

    @app.get("/app")
    async def mini_app() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/overview")
    async def api_overview(u: dict = Depends(user)) -> dict:
        data = await overview(svc, recent_limit=12, days=14)
        async with db.session() as s:
            pending = list((await s.execute(
                select(Video).where(Video.status == VideoStatus.PENDING_APPROVAL).order_by(Video.id).limit(10)
            )).scalars().all())
        names = {c["id"]: c["name"] for c in data["channels"]}
        data["approvals"] = [
            {"id": v.id, "title": v.title, "channel": names.get(v.channel_id, "?"), "duration": round(v.duration),
             "part": f"{v.part_index}/{v.part_count}" if v.part_count > 1 else "", "text": v.text[:600]}
            for v in pending
        ]
        data["user"] = {"first_name": u.get("first_name", "")}
        return data

    @app.post("/api/system/pause")
    async def api_pause(u: dict = Depends(user)) -> dict:
        paused = not await svc.is_paused()
        await svc.set_paused(paused)
        return {"paused": paused}

    @app.post("/api/channels/{cid}/toggle")
    async def api_toggle(cid: int, u: dict = Depends(user)) -> dict:
        ch = await svc.get_channel(cid)
        if ch is None:
            raise HTTPException(404, "Chaîne introuvable")
        ch = await svc.update_channel(cid, active=not ch.active)
        await db.log_event(f"{ch.name} {'activée' if ch.active else 'mise en pause'} depuis la Mini App", "info", cid)
        return {"active": ch.active}

    @app.post("/api/channels/{cid}/generate")
    async def api_generate(cid: int, u: dict = Depends(user)) -> dict:
        missing = svc.missing_prereqs()
        if missing:
            raise HTTPException(400, "Il manque : " + ", ".join(missing))
        ch = await svc.get_channel(cid)
        if ch is None:
            raise HTTPException(404, "Chaîne introuvable")
        progress = await svc.notifier.progress(f"Génération · {ch.name}", chat_id=int(u["id"]))
        job = svc.enqueue_generate(cid, progress=progress, label=f"Mini App · {ch.name}")
        return {"job": job.id}

    @app.post("/api/channels/{cid}/publish-next")
    async def api_publish_next(cid: int, u: dict = Depends(user)) -> dict:
        v = await svc.next_ready_video(cid)
        if v is None:
            raise HTTPException(400, "Aucune vidéo prête pour cette chaîne")
        svc.enqueue_publish(v.id, cid)
        return {"video": v.id}

    @app.post("/api/videos/{vid}/{action}")
    async def api_video(vid: int, action: str, u: dict = Depends(user)) -> dict:
        v = await svc.get_video(vid)
        if v is None:
            raise HTTPException(404, "Vidéo introuvable")
        if action == "approve":
            await svc.approve(vid)
        elif action == "publish":
            ch = await svc.get_channel(v.channel_id)
            if not ch or not ch.platforms():
                raise HTTPException(400, "Aucune plateforme liée pour cette chaîne")
            if v.status == VideoStatus.PENDING_APPROVAL:
                await svc.approve(vid)
            svc.enqueue_publish(vid, v.channel_id)
        elif action == "discard":
            await svc.discard(vid)
        else:
            raise HTTPException(400, "Action inconnue")
        notifier = svc.notifier
        if hasattr(notifier, "refresh_video_messages"):
            await notifier.refresh_video_messages(vid)
        return {"ok": True}

    @app.get("/media/thumb/{vid}")
    async def media_thumb(vid: int, u: dict = Depends(user)) -> FileResponse:
        v = await svc.get_video(vid)
        if not v or not v.thumb_path or not Path(v.thumb_path).exists():
            raise HTTPException(404)
        return FileResponse(v.thumb_path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/media/video/{vid}")
    async def media_video(vid: int, u: dict = Depends(user)) -> FileResponse:
        v = await svc.get_video(vid)
        path = v and (v.preview_path or v.file_path)
        if not path or not Path(path).exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="video/mp4")

    @app.get("/oauth/youtube")
    async def oauth_youtube(request: Request) -> HTMLResponse:
        q = request.query_params
        if q.get("error"):
            return _page("Autorisation refusée", html.escape(q["error"]), ok=False)
        try:
            platform, ch, account = await svc.complete_oauth(q.get("state", ""), q.get("code", ""))
        except Exception as exc:
            return _page("Échec de la liaison", html.escape(str(exc)), ok=False)
        await svc.notifier.notify(f"<b>YouTube lié</b> · {html.escape(ch.name)}\n\nCompte : {html.escape(account)}", "success", ch.id)
        return _page("Compte lié", f"La chaîne <b>{html.escape(ch.name)}</b> publiera sur <b>{html.escape(account)}</b>."
                     "<br>Tu peux fermer cette page et revenir sur Telegram.")

    return app


def build_server(svc: Services):
    """Serveur uvicorn qui laisse la gestion des signaux (Ctrl+C, SIGTERM) au processus principal."""
    import contextlib

    import uvicorn

    class _Server(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):  # uvicorn récent
            yield

        def install_signal_handlers(self) -> None:  # anciennes versions d'uvicorn
            return None

    config = uvicorn.Config(
        create_app(svc), host=svc.settings.webapp_host, port=svc.settings.webapp_port,
        log_level="warning", proxy_headers=True, forwarded_allow_ips="*",
    )
    return _Server(config)
