"""Localisation de yt-dlp (environnement du service) et moteur JavaScript pour YouTube."""

from __future__ import annotations

import sys

from storyforge import tools


def test_ytdlp_found_next_to_the_python_of_the_service(tmp_path, monkeypatch):
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("")
    (venv_bin / "yt-dlp").write_text("")
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)  # PATH du service : sans .venv/bin
    assert tools.ytdlp_command() == [str(venv_bin / "yt-dlp")]

    (venv_bin / "yt-dlp").unlink()
    monkeypatch.setattr(tools.importlib.util, "find_spec", lambda name: object())
    assert tools.ytdlp_command() == [str(venv_bin / "python"), "-m", "yt_dlp"]
    monkeypatch.setattr(tools.importlib.util, "find_spec", lambda name: None)
    assert tools.ytdlp_command() is None


def test_js_runtime_prefers_deno_then_node(tmp_path, monkeypatch):
    paths = {"deno": "/usr/bin/deno", "node": "/usr/bin/node"}
    monkeypatch.setattr(tools.shutil, "which", lambda name: paths.get(name))
    assert tools.js_runtime_args() == []  # Deno : activé par défaut dans yt-dlp
    paths.pop("deno")
    assert tools.js_runtime_args() == ["--js-runtimes", "node:/usr/bin/node"]
    paths.clear()
    nvm = tmp_path / ".nvm" / "versions" / "node" / "v22.1.0" / "bin"
    nvm.mkdir(parents=True)
    (nvm / "node").write_text("")
    monkeypatch.setattr(tools.Path, "home", lambda: tmp_path)
    assert tools.js_runtime_args() == ["--js-runtimes", f"node:{nvm / 'node'}"]


def test_ytdlp_hints():
    assert "datacenter" in tools.ytdlp_hint("ERROR: Sign in to confirm you're not a bot")
    assert "pas pris en charge" in tools.ytdlp_hint("ERROR: Unsupported URL: https://x")
    assert tools.ytdlp_hint("autre erreur") == "autre erreur"


# ---------------------------------------------------------------- proxies gratuits Proxora
import os  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from storyforge.bot import handlers  # noqa: E402

from .conftest import needs_ffmpeg  # noqa: E402
from .test_bot import click, make_ctx, type_text  # noqa: E402

@pytest.fixture(autouse=True)
def fresh_proxy_state():
    tools._bad.clear()
    tools._good.clear()
    tools._proxora.update(at=0.0, items=[], error="")
    yield


ITEMS = [
    {"host": "1.1.1.1", "port": 80, "protocol": "http", "exit_type": "hosting", "https": True, "score": 100},
    {"host": "2.2.2.2", "port": 1080, "protocol": "socks5", "exit_type": "isp", "https": True, "score": 70},
    {"host": "2.2.2.2", "port": 1081, "protocol": "socks5", "exit_type": "isp", "https": True, "score": 90},
    {"host": "3.3.3.3", "port": 8080, "protocol": "http", "exit_type": "mobile", "https": False, "score": 99},
    {"host": "4.4.4.4", "port": 3128, "protocol": "http", "exit_type": "isp", "https": True, "tampered": True},
]


def test_proxora_ranking_prefers_residential_and_clean():
    assert tools._rank(ITEMS) == ["socks5://2.2.2.2:1081", "http://3.3.3.3:8080", "http://1.1.1.1:80"]


async def test_proxora_fetch_is_cached(monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json={"items": ITEMS if request.url.params.get("exit") == "isp" else []})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    first = await tools.proxora_proxies()
    assert first[0] == "socks5://2.2.2.2:1081" and calls[0]["format"] == "json" and calls[0]["https"] == "true"
    n = len(calls)
    assert await tools.proxora_proxies() == first and len(calls) == n  # cache 10 min (limite par IP)
    await tools.proxora_proxies(force=True)
    assert len(calls) == 2 * n
    assert tools.proxora_status()["count"] == 3


@needs_ffmpeg
async def test_direct_first_then_proxora_proxies(svc, tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    fake = tmp_path / "yt-dlp"
    fake.write_text(f"""#!/bin/sh
echo "$@" >> {log}
case "$*" in
  *gone*) echo "ERROR: Video unavailable" >&2; exit 1 ;;
  *http://good:8080*) echo "/tmp/ok.mp4"; exit 0 ;;
  *vimeo*) echo "/tmp/direct.mp4"; exit 0 ;;
  *) echo "ERROR: Sign in to confirm you're not a bot" >&2; exit 1 ;;
esac
""")
    os.chmod(fake, 0o755)
    monkeypatch.setattr(tools, "ytdlp_command", lambda: [str(fake)])
    fetched = []

    async def proxora(force=False):
        fetched.append(force)
        return ["http://bad1:8080", "http://bad2:8080", "http://good:8080"]

    monkeypatch.setattr(tools, "proxora_proxies", proxora)
    # Un site qui ne bloque pas : téléchargement direct, la liste Proxora n'est même pas lue
    res = await tools.run_ytdlp(svc.settings, ["--", "https://vimeo.com/1"])
    assert res.ok and res.proxy is None and res.attempts == 1 and not fetched
    # YouTube bloque le serveur : direct, puis proxies jusqu'à celui qui marche
    res = await tools.run_ytdlp(svc.settings, ["--", "https://youtu.be/x"])
    assert res.ok and res.proxy == "http://good:8080" and res.attempts == 4
    assert "--socket-timeout" in log.read_text()
    assert set(tools._bad) == {"http://bad1:8080", "http://bad2:8080"} and tools._good == ["http://good:8080"]
    # Les proxies en échec sont mis de côté, celui qui a marché passe en premier
    res = await tools.run_ytdlp(svc.settings, ["--", "https://youtu.be/x"])
    assert res.ok and res.attempts == 2
    # Erreur définitive : pas de rotation
    res = await tools.run_ytdlp(svc.settings, ["--", "https://youtu.be/gone"])
    assert not res.ok and res.attempts == 1


@needs_ffmpeg
async def test_youtube_settings_screen(svc, tmp_path, monkeypatch):
    ctx, bot = make_ctx(svc)

    async def proxora(force=False):
        tools._proxora.update(at=1.0, items=["http://good:8080"], error="")
        return ["http://good:8080"]

    monkeypatch.setattr(tools, "proxora_proxies", proxora)
    q = await click(ctx, "yt")
    assert "Proxora" in q.edits[-1] and "1 disponibles" in q.edits[-1]
    assert "cookies" not in q.edits[-1].lower()
    q = await click(ctx, "ytr")
    assert q.answers[-1] == "1 proxies gratuits disponibles"
    fake = tmp_path / "yt-dlp"
    fake.write_text("#!/bin/sh\necho 'Me at the zoo'\n")
    os.chmod(fake, 0o755)
    monkeypatch.setattr(handlers, "ytdlp_command", lambda: [str(fake)])
    monkeypatch.setattr(tools, "ytdlp_command", lambda: [str(fake)])
    edits = []

    async def send_message(chat_id, text, **kw):
        async def edit_text(t, **k):
            edits.append(t)
        return SimpleNamespace(edit_text=edit_text)

    monkeypatch.setattr(bot, "send_message", send_message)
    await click(ctx, "ytt")
    assert "YouTube répond en direct" in edits[-1] and "Me at the zoo" in edits[-1]


@needs_ffmpeg
async def test_link_sent_anytime_goes_to_the_bank(svc, tmp_path, monkeypatch):
    import asyncio

    fake = tmp_path / "yt-dlp"
    # yt-dlp factice : copie un fond existant à l'endroit demandé (-o) et affiche le chemin
    fake.write_text(f"""#!/bin/sh
dest=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && dest="$2"; shift; done
dir=$(dirname "$dest"); mkdir -p "$dir"
cp {svc.settings.backgrounds_dir}/cooking/long.mp4 "$dir/fond-abc.mp4"
echo "$dir/fond-abc.mp4"
""")
    os.chmod(fake, 0o755)
    monkeypatch.setattr(tools, "ytdlp_command", lambda: [str(fake)])
    monkeypatch.setattr(handlers, "ytdlp_command", lambda: [str(fake)])
    ctx, bot = make_ctx(svc)
    edits = []

    async def send_message(chat_id, text, **kw):
        edits.append(text)

        async def edit_text(t, **k):
            edits.append(t)
        return SimpleNamespace(edit_text=edit_text, message_id=1)

    monkeypatch.setattr(bot, "send_message", send_message)

    async def wait_for(text):
        for _ in range(100):
            if any(text in e for e in edits):
                return True
            await asyncio.sleep(0.05)
        return False

    # Lien envoyé sans passer par un menu (même après l'écran des réglages YouTube)
    await click(ctx, "yt")
    m = await type_text(ctx, "https://www.youtube.com/watch?v=abc")
    assert "Que faire de ce lien" in m.replies[-1]
    await click(ctx, "lkc:0")  # cooking
    assert await wait_for("✓ Ajoutée : fond-abc.mp4")
    assert (svc.settings.backgrounds_dir / "cooking" / "fond-abc.mp4").exists()

    # Nouvelle catégorie créée pour le lien
    edits.clear()
    await type_text(ctx, "https://www.youtube.com/watch?v=def")
    await click(ctx, "lkn")
    await type_text(ctx, "Satisfying")
    assert await wait_for("Téléchargement dans « satisfying »")
    assert await wait_for("✓ Ajoutée")
    assert (svc.settings.backgrounds_dir / "satisfying" / "fond-abc.mp4").exists()

    # Une erreur inattendue dans une tâche de fond est signalée
    async def boom():
        raise RuntimeError("disque plein")

    await handlers.background(ctx, 42, boom())
    assert any("Erreur inattendue : disque plein" in e for e in edits)


@needs_ffmpeg
async def test_download_progress_cancel_and_stall(svc, tmp_path, monkeypatch):
    import asyncio

    src = svc.settings.backgrounds_dir / "cooking" / "long.mp4"
    fake = tmp_path / "yt-dlp"
    # Progression, puis fichier final. « slow » : reste muet (proxy mort). « hang » : très long (pour annuler).
    fake.write_text(f"""#!/bin/sh
dest=""; for a in "$@"; do [ "$prev" = "-o" ] && dest="$a"; prev="$a"; done
dir=$(dirname "$dest"); mkdir -p "$dir"
case "$*" in *http://slow:1*) echo "SFPROG   5.0%|1.00KiB/s|99:00|50.00MiB"; sleep 30; exit 1 ;; esac
case "$*" in *hang*) echo "SFPROG  12.0%|1.00MiB/s|10:00|50.00MiB"; sleep 30; exit 1 ;; esac
case "$*" in *--proxy*) ;; *blocked*) echo "ERROR: Sign in to confirm you're not a bot" >&2; exit 1 ;; esac
for p in 10.0 55.5 100.0; do echo "SFPROG  $p%|2.10MiB/s|00:05|50.00MiB"; sleep 0.1; done
touch "$dir/partiel.f137.mp4.part"
cp {src} "$dir/Fond_ASMR-xyz.mp4"
echo "$dir/Fond_ASMR-xyz.mp4"
""")
    os.chmod(fake, 0o755)
    monkeypatch.setattr(tools, "ytdlp_command", lambda: [str(fake)])
    monkeypatch.setattr(handlers, "ytdlp_command", lambda: [str(fake)])
    ctx, bot = make_ctx(svc)
    edits = []

    async def send_message(chat_id, text, **kw):
        edits.append(text)

        async def edit_text(t, **k):
            edits.append(t)
        return SimpleNamespace(edit_text=edit_text, message_id=1)

    monkeypatch.setattr(bot, "send_message", send_message)
    busy_seen = []

    async def spy_progress_hold():
        busy_seen.append(svc.jobs.busy_file.exists())

    real_run = tools.run_ytdlp

    async def run_spy(*a, **kw):
        await spy_progress_hold()
        return await real_run(*a, **kw)

    monkeypatch.setattr(handlers, "run_ytdlp", run_spy)

    # Progression affichée, fichier déplacé dans la banque, morceaux partiels jamais dans la banque
    await handlers._download_bg(ctx, 42, "https://youtu.be/ok", "asmr")
    assert busy_seen == [True] and not svc.jobs.busy_file.exists()  # mise à jour bloquée pendant le téléchargement
    assert any("%" in e and "Téléchargement dans « asmr »" in e for e in edits)
    assert any(e.startswith("✓ Ajoutée : Fond_ASMR-xyz.mp4") for e in edits)
    bank = svc.settings.backgrounds_dir / "asmr"
    assert sorted(p.name for p in bank.iterdir()) == ["Fond_ASMR-xyz.mp4"]
    assert not list(svc.settings.work_dir.glob("dl-*"))

    # Annulation : le bouton coupe yt-dlp tout de suite
    edits.clear()
    task = asyncio.create_task(handlers._download_bg(ctx, 42, "https://youtu.be/hang", "asmr"))
    for _ in range(50):
        await asyncio.sleep(0.05)
        if handlers.DOWNLOADS:
            break
    token = next(iter(handlers.DOWNLOADS))
    await click(ctx, f"dlx:{token}")
    await asyncio.wait_for(task, timeout=5)
    assert edits[-1] == "✕ Téléchargement annulé." and not handlers.DOWNLOADS

    # Bloqué en direct, proxy muet coupé faute de progression, le suivant prend le relais
    async def proxora(force=False):
        return ["http://slow:1", "http://fast:2"]

    monkeypatch.setattr(tools, "proxora_proxies", proxora)
    res = await tools.run_ytdlp(svc.settings, ["-o", str(tmp_path / "x" / "%(title)s.%(ext)s"), "--", "blocked"],
                                on_progress=lambda p: asyncio.sleep(0), stall=1)
    assert res.ok and res.proxy == "http://fast:2" and res.attempts == 3
    assert "http://slow:1" in tools._bad


def test_parse_progress_from_real_ytdlp_lines():
    p = tools.parse_progress("SFPROG  45.3%|  3.88MiB/s|00:12|2.72MiBNA")
    assert p == {"percent": 45.3, "speed": "3.88MiB/s", "eta": "00:12", "size": "2.72MiB"}
    p = tools.parse_progress("SFPROG   0.0%|Unknown B/s|Unknown|N/A~ 512.00MiB")
    assert p["speed"] == "" and p["eta"] == "" and p["size"] == "512.00MiB"
    assert tools.parse_progress("[youtube] Extracting URL") is None
