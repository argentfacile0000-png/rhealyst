"""Serveur web : Telegram Mini App, page de connexion des comptes et retours OAuth."""

from __future__ import annotations

import html
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .. import db
from ..models import Video, VideoFormat, VideoStatus
from ..publishers import PLATFORM_LABELS
from ..stats import overview
from . import pages
from .auth import validate_init_data

if TYPE_CHECKING:
    from ..core import Services

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
MAX_BODY = 64 * 1024  # aucune route n'accepte de gros envois

# Politiques de sécurité du contenu : scripts uniquement servis par StoryForge (et Telegram pour la Mini App),
# aucune donnée envoyée ailleurs, pages non intégrables dans un autre site (sauf la Mini App dans Telegram Web).
_CSP_COMMON = ("default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
               "font-src https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; "
               "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'")
CSP_APP = f"{_CSP_COMMON}; script-src 'self' https://telegram.org; frame-ancestors https://web.telegram.org https://*.telegram.org"
CSP_PAGE = f"{_CSP_COMMON}; script-src 'self'; frame-ancestors 'none'"
CSP_DATA = "default-src 'none'; frame-ancestors 'none'"


class FailureGuard:
    """Bloque temporairement une adresse IP qui accumule les accès refusés (force brute, scans)."""

    LIMIT = 30  # refus tolérés…
    WINDOW = 600  # …sur 10 minutes
    BAN = 1800  # puis blocage de 30 minutes

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._banned: dict[str, float] = {}

    def banned(self, ip: str) -> bool:
        until = self._banned.get(ip)
        if until is None:
            return False
        if until > time.monotonic():
            return True
        self._banned.pop(ip, None)
        return False

    def fail(self, ip: str) -> None:
        now = time.monotonic()
        hits = [t for t in self._fails.get(ip, ()) if now - t < self.WINDOW]
        hits.append(now)
        if len(hits) >= self.LIMIT:
            self._fails.pop(ip, None)
            self._banned[ip] = now + self.BAN
            log.warning("Adresse %s bloquée 30 min : trop d'accès refusés", ip)
            return
        self._fails[ip] = hits
        if len(self._fails) > 10000:  # borne la mémoire en cas d'attaque distribuée
            self._fails.clear()


def create_app(svc: Services) -> FastAPI:
    app = FastAPI(title="StoryForge", docs_url=None, redoc_url=None, openapi_url=None)
    settings = svc.settings
    guard = FailureGuard()
    app.state.failure_guard = guard
    hsts = settings.public_base.startswith("https://")

    @app.middleware("http")
    async def security(request: Request, call_next):
        ip = request.client.host if request.client else "?"
        if guard.banned(ip):
            response: Response = PlainTextResponse("Trop de tentatives refusées. Réessaie plus tard.", status_code=429)
        else:
            length = request.headers.get("content-length", "")
            if length and (not length.isdigit() or int(length) > MAX_BODY):
                response = PlainTextResponse("Requête refusée.", status_code=413)
            else:
                response = await call_next(request)
            if response.status_code in (401, 403):
                guard.fail(ip)
        path = request.url.path
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if hsts:
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        if path == "/app":
            h.setdefault("Content-Security-Policy", CSP_APP)
        elif path == "/" or path.startswith(("/connect", "/oauth", "/legal")):
            h.setdefault("Content-Security-Policy", CSP_PAGE)
            h.setdefault("X-Frame-Options", "DENY")
        elif not path.startswith("/static/"):
            h.setdefault("Content-Security-Policy", CSP_DATA)
            h.setdefault("X-Frame-Options", "DENY")
        return response

    def user(request: Request) -> dict:
        """Administrateur authentifié par la signature Telegram de la Mini App (en-tête uniquement)."""
        u = validate_init_data(request.headers.get("X-Telegram-Init-Data", ""), settings.telegram_bot_token)
        if not u or int(u.get("id", 0)) not in settings.admin_ids:
            raise HTTPException(status_code=401, detail="Non autorisé")
        return u

    def media_viewer(request: Request) -> int:
        """Lecture des aperçus : jeton dédié, temporaire, qui ne permet aucune action."""
        uid = svc.verify_media_session(request.query_params.get("mt", ""))
        if uid is None:
            raise HTTPException(status_code=401, detail="Non autorisé")
        return uid

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def root() -> HTMLResponse:
        return pages.landing(settings.public_base)

    @app.get("/legal/{kind}")
    async def legal(kind: str) -> HTMLResponse:
        """Pages exigées par TikTok et Meta pour créer une app (conditions, confidentialité, suppression)."""
        if kind not in pages.LEGAL_TITLES:
            raise HTTPException(404)
        return pages.legal(kind, base=settings.public_base, contact=settings.legal_contact)

    @app.get("/healthz")
    async def health() -> dict:
        return {"ok": True}

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
        data["media_token"] = svc.media_session_token(int(u["id"]))
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
    async def api_generate(cid: int, kind: str = VideoFormat.VERTICAL, u: dict = Depends(user)) -> dict:
        missing = svc.missing_prereqs()
        if missing:
            raise HTTPException(400, "Il manque : " + ", ".join(missing))
        ch = await svc.get_channel(cid)
        if ch is None:
            raise HTTPException(404, "Chaîne introuvable")
        if kind not in (VideoFormat.VERTICAL, VideoFormat.HORIZONTAL):
            raise HTTPException(400, "Format inconnu")
        title = f"{'Vidéo longue' if kind == VideoFormat.HORIZONTAL else 'Génération'} · {ch.name}"
        progress = await svc.notifier.progress(title, chat_id=int(u["id"]))
        job = svc.enqueue_generate(cid, kind=kind, progress=progress, label=f"Mini App · {title}")
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
        elif action in ("archive", "unarchive"):
            try:
                await (svc.archive(vid) if action == "archive" else svc.unarchive(vid))
            except ValueError as exc:
                raise HTTPException(400, str(exc))
        else:
            raise HTTPException(400, "Action inconnue")
        notifier = svc.notifier
        if hasattr(notifier, "refresh_video_messages"):
            await notifier.refresh_video_messages(vid)
        return {"ok": True}

    @app.get("/media/thumb/{vid}")
    async def media_thumb(vid: int, viewer: int = Depends(media_viewer)) -> FileResponse:
        v = await svc.get_video(vid)
        if not v or not v.thumb_path or not Path(v.thumb_path).exists():
            raise HTTPException(404)
        return FileResponse(v.thumb_path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/media/public/{vid}/{exp}/{sig}.mp4")
    async def media_public(vid: int, exp: int, sig: str) -> FileResponse:
        """Adresse signée et temporaire : Instagram télécharge la vidéo à publier ici."""
        if not svc.verify_media(vid, exp, sig):
            raise HTTPException(403)
        v = await svc.get_video(vid)
        if not v or not v.file_path or not Path(v.file_path).exists():
            raise HTTPException(404)
        return FileResponse(v.file_path, media_type="video/mp4")

    @app.get("/media/video/{vid}")
    async def media_video(vid: int, viewer: int = Depends(media_viewer)) -> FileResponse:
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
        label = PLATFORM_LABELS[res.platform]
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
        if q.get("done") in PLATFORM_LABELS:
            flash = ("success", f"{PLATFORM_LABELS[q['done']]} est connecté.")
        elif q.get("off") in PLATFORM_LABELS:
            flash = ("info", f"{PLATFORM_LABELS[q['off']]} est déconnecté.")
        elif q.get("error"):
            flash = ("error", html.escape(q["error"][:300]))
        return pages.portal(
            ch, token, exp,
            problems={p: svc.platform_problem(p, ch.id) for p in PLATFORM_LABELS},
            automatic={p: svc.oauth_is_automatic(p, ch.id) for p in PLATFORM_LABELS},
            flash=flash, tz=settings.timezone,
        )

    @app.get("/connect/{token}/{platform}")
    async def connect_start(token: str, platform: str) -> Response:
        ctx = await portal_context(token)
        if not ctx or platform not in PLATFORM_LABELS:
            return pages.expired(tg_url())
        ch, _ = ctx
        problem = svc.platform_problem(platform, ch.id)
        if problem:
            return RedirectResponse(f"/connect/{token}?error={quote(problem)}", status_code=303)
        try:
            url = svc.auth_url(platform, ch.id, portal=token)
        except Exception as exc:
            return RedirectResponse(f"/connect/{token}?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(url, status_code=303)

    @app.post("/connect/{token}/{platform}/paste")
    async def connect_paste(token: str, platform: str, request: Request) -> Response:
        if not await portal_context(token):
            return pages.expired(tg_url())
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 16384:
                raise HTTPException(413, "Requête trop volumineuse")
        form = parse_qs(body.decode("utf-8", "replace"))
        try:
            res = await svc.complete_oauth_from_url((form.get("url") or [""])[0])
        except Exception as exc:
            return RedirectResponse(f"/connect/{token}?error={quote(str(exc))}", status_code=303)
        await connected(res)
        return RedirectResponse(f"/connect/{token}?done={res.platform}", status_code=303)

    @app.post("/connect/{token}/{platform}/disconnect")
    async def connect_disconnect(token: str, platform: str) -> Response:
        ctx = await portal_context(token)
        if not ctx or platform not in PLATFORM_LABELS:
            return pages.expired(tg_url())
        await svc.unlink(ctx[0].id, platform)
        return RedirectResponse(f"/connect/{token}?off={platform}", status_code=303)

    @app.get("/oauth/{platform}")
    async def oauth_callback(platform: str, request: Request) -> HTMLResponse:
        q = request.query_params
        if platform not in PLATFORM_LABELS:
            raise HTTPException(404)
        name = PLATFORM_LABELS[platform]
        if q.get("error"):
            info = svc.oauth.pop(q.get("state", ""))
            reasons = {"access_denied": "l'accès a été refusé sur la page d'autorisation",
                       "invalid_scope": "les autorisations demandées ne sont pas activées dans l'app"}
            reason = reasons.get(q["error"]) or q.get("error_description") or q["error"]
            return pages.result(ok=False, heading="Autorisation refusée",
                                message=f"{name} n'a pas donné l'accès : {html.escape(reason)}.",
                                portal_url=portal_url(info.get("portal", "") if info else ""), telegram_url=tg_url())
        try:
            res = await svc.complete_oauth(q.get("state", ""), q.get("code", ""), platform=platform)
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
        log_level="warning", proxy_headers=True, server_header=False, limit_concurrency=100,
        # Seul le proxy local (Caddy) peut indiquer l'adresse réelle du visiteur.
        forwarded_allow_ips=svc.settings.webapp_trusted_proxies,
    )
    return _Server(config)
