"""Serveur web : Telegram Mini App, page de connexion des comptes et retours OAuth."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from .. import db
from ..models import Video, VideoStatus
from ..stats import overview
from . import pages
from .auth import validate_init_data

if TYPE_CHECKING:
    from ..core import Services

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


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

    # ------------------------------------------------------------------ connexion des comptes
    def tg_url() -> str:
        return f"https://t.me/{svc.bot_username}" if svc.bot_username else ""

    def portal_url(token: str) -> str:
        return f"/connect/{token}" if token and svc.verify_portal_token(token) else ""

    async def portal_context(token: str):
        checked = svc.verify_portal_token(token)
        if not checked:
            return None
        ch = await svc.get_channel(checked[0])
        return (ch, checked[1]) if ch else None

    async def connected(res) -> None:
        label = "YouTube" if res.platform == "youtube" else "TikTok"
        await svc.notifier.notify(
            f"<b>{label} connecté</b> · {html.escape(res.channel.name)}\n\nCompte : {html.escape(res.account)}",
            "success", res.channel.id,
        )

    @app.post("/api/channels/{cid}/connect-link")
    async def api_connect_link(cid: int, u: dict = Depends(user)) -> dict:
        if not svc.portal_available():
            raise HTTPException(400, "Page de connexion indisponible : configure WEBAPP_ENABLED et PUBLIC_URL (https)")
        if await svc.get_channel(cid) is None:
            raise HTTPException(404, "Chaîne introuvable")
        return {"url": svc.portal_link(cid)}

    @app.get("/connect/{token}")
    async def connect_portal(token: str, request: Request) -> HTMLResponse:
        ctx = await portal_context(token)
        if not ctx:
            return pages.expired(tg_url())
        ch, exp = ctx
        flash = None
        q = request.query_params
        if q.get("done"):
            name = "YouTube" if q["done"] == "youtube" else "TikTok"
            flash = ("success", f"{name} est connecté.")
        elif q.get("off"):
            name = "YouTube" if q["off"] == "youtube" else "TikTok"
            flash = ("info", f"{name} est déconnecté.")
        elif q.get("error"):
            flash = ("error", html.escape(q["error"][:300]))
        return pages.portal(
            ch, token, exp,
            problems={p: svc.platform_problem(p, ch.id) for p in ("youtube", "tiktok")},
            automatic={p: svc.oauth_is_automatic(p, ch.id) for p in ("youtube", "tiktok")},
            flash=flash, tz=settings.timezone,
        )

    @app.get("/connect/{token}/{platform}")
    async def connect_start(token: str, platform: str) -> Response:
        ctx = await portal_context(token)
        if not ctx or platform not in ("youtube", "tiktok"):
            return pages.expired(tg_url())
        ch, _ = ctx
        problem = svc.platform_problem(platform, ch.id)
        if problem:
            return RedirectResponse(f"/connect/{token}?error={quote(problem)}", status_code=303)
        try:
            url = svc.youtube_auth_url(ch.id, portal=token) if platform == "youtube" else svc.tiktok_auth_url(ch.id, portal=token)
        except Exception as exc:
            return RedirectResponse(f"/connect/{token}?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(url, status_code=303)

    @app.post("/connect/{token}/{platform}/paste")
    async def connect_paste(token: str, platform: str, request: Request) -> Response:
        if not await portal_context(token):
            return pages.expired(tg_url())
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        try:
            res = await svc.complete_oauth_from_url((form.get("url") or [""])[0])
        except Exception as exc:
            return RedirectResponse(f"/connect/{token}?error={quote(str(exc))}", status_code=303)
        await connected(res)
        return RedirectResponse(f"/connect/{token}?done={res.platform}", status_code=303)

    @app.post("/connect/{token}/{platform}/disconnect")
    async def connect_disconnect(token: str, platform: str) -> Response:
        ctx = await portal_context(token)
        if not ctx or platform not in ("youtube", "tiktok"):
            return pages.expired(tg_url())
        await svc.unlink(ctx[0].id, platform)
        return RedirectResponse(f"/connect/{token}?off={platform}", status_code=303)

    @app.get("/oauth/{platform}")
    async def oauth_callback(platform: str, request: Request) -> HTMLResponse:
        q = request.query_params
        if platform not in ("youtube", "tiktok"):
            raise HTTPException(404)
        name = "YouTube" if platform == "youtube" else "TikTok"
        if q.get("error"):
            info = svc.oauth.pop(q.get("state", ""))
            reasons = {"access_denied": "l'accès a été refusé sur la page d'autorisation",
                       "invalid_scope": "les autorisations demandées ne sont pas activées dans l'app"}
            reason = reasons.get(q["error"]) or q.get("error_description") or q["error"]
            return pages.result(ok=False, heading="Autorisation refusée",
                                message=f"{name} n'a pas donné l'accès : {html.escape(reason)}.",
                                portal_url=portal_url(info.get("portal", "") if info else ""), telegram_url=tg_url())
        try:
            res = await svc.complete_oauth(q.get("state", ""), q.get("code", ""))
        except Exception as exc:
            return pages.result(ok=False, heading="Connexion impossible", message=html.escape(str(exc)),
                                telegram_url=tg_url())
        await connected(res)
        return pages.result(
            ok=True, heading=f"{name} connecté",
            message=f"La chaîne <b>{html.escape(res.channel.name)}</b> publiera sur "
                    f"<b>{html.escape(res.account)}</b>. Tu peux fermer cette page.",
            portal_url=portal_url(res.portal), telegram_url=tg_url(),
        )

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
