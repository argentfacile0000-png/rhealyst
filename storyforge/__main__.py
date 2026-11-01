"""Point d'entrée : python -m storyforge [run|demo|story|check]."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys

from .config import get_settings


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s · %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "telegram.ext", "apscheduler", "googleapiclient", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def run() -> None:
    from .bot.app import announce, build_application
    from .core import Services

    settings = get_settings()
    svc = Services(settings)
    await svc.start()
    app = build_application(svc)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    web_server = web_task = None
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await announce(app, svc)
        if settings.webapp_enabled:
            from .webapp.server import build_server

            web_server = build_server(svc)
            web_task = asyncio.create_task(web_server.serve(), name="webapp")
            logging.info("Mini App sur http://%s:%s/app (public : %s)", settings.webapp_host, settings.webapp_port,
                         settings.webapp_url or "PUBLIC_URL non défini")
        logging.info("🎬 StoryForge en ligne — ouvre ton bot Telegram et tape /start")
        await stop.wait()
        logging.info("Arrêt en cours…")
        if web_server and web_task:
            web_server.should_exit = True
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(web_task, timeout=10)
        await app.updater.stop()
        await app.stop()
    await svc.stop()


async def demo(style: str, card: bool) -> None:
    from .demo import run_demo

    settings = get_settings()
    settings.ensure_dirs()
    out = await run_demo(settings, style=style, card=card)
    print(f"✅ Vidéo de démonstration : {out}")


async def story(language: str, fmt: str | None, parts: int, seconds: int) -> None:
    from .story import StoryGenerator

    gen = StoryGenerator(get_settings())
    s = await gen.generate(language=language, formats=[fmt] if fmt else None, parts=parts, seconds_per_part=seconds,
                           voice_speed=1.1, speedup=1.2)
    print(json.dumps({
        "format": s.format, "title": s.title, "youtube_title": s.youtube_title, "words": s.word_count,
        "hashtags": s.hashtags, "tiktok_caption": s.tiktok_caption,
    }, ensure_ascii=False, indent=2))
    for i, p in enumerate(s.parts, 1):
        print(f"\n===== PARTIE {i} =====\n{p}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="storyforge", description="Usine à vidéos storytime pilotée par Telegram")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="Lance le bot, le planificateur et la Mini App (défaut)")
    d = sub.add_parser("demo", help="Rendu vidéo de démonstration hors-ligne")
    d.add_argument("--style", choices=["pop", "trio"], default="pop")
    d.add_argument("--card", action="store_true", help="Ajoute la carte d'accroche")
    st = sub.add_parser("story", help="Génère une histoire (test du prompt, nécessite OPENAI_API_KEY)")
    st.add_argument("--lang", default="fr")
    st.add_argument("--format", default=None)
    st.add_argument("--parts", type=int, default=1)
    st.add_argument("--seconds", type=int, default=80)
    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        if args.cmd == "demo":
            asyncio.run(demo(args.style, args.card))
        elif args.cmd == "story":
            asyncio.run(story(args.lang, args.format, args.parts, args.seconds))
        else:
            asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
