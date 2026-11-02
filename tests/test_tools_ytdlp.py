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


# ---------------------------------------------------------------- cookies et proxy
import os  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from storyforge.bot import handlers  # noqa: E402

from .conftest import needs_ffmpeg  # noqa: E402
from .test_bot import FakeMessage, click, make_ctx, type_text  # noqa: E402

COOKIES = (b"# Netscape HTTP Cookie File\n"
           b".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSAPISID\tabc\n"
           b"#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1893456000\t__Secure-3PSID\tdef\n"
           b".google.com\tTRUE\t/\tTRUE\t1893456000\tSID\tghi\n")


def test_parse_cookies_and_proxy():
    text, count = tools.parse_cookies(COOKIES)
    assert count == 3 and text.startswith("# Netscape")
    text, _ = tools.parse_cookies(COOKIES.split(b"\n", 1)[1])  # sans en-tête : ajouté
    assert text.startswith("# Netscape HTTP Cookie File\n")
    with pytest.raises(ValueError, match="Netscape"):
        tools.parse_cookies(b'{"cookies": []}')
    with pytest.raises(ValueError, match="youtube.com"):
        tools.parse_cookies(b".example.com\tTRUE\t/\tTRUE\t0\ta\tb\n")
    assert tools.check_proxy(" socks5://h:1080 ") == "socks5://h:1080"
    with pytest.raises(ValueError):
        tools.check_proxy("ftp://h")
    assert tools.mask_proxy("http://user:secret@h:8080") == "http://•••@h:8080"


@needs_ffmpeg
async def test_ytdlp_settings_from_the_bot(svc, tmp_path, monkeypatch):
    svc.settings.google_client_secrets_file = tmp_path / "secrets" / "google_client_secret.json"
    ctx, bot = make_ctx(svc)
    q = await click(ctx, "yt")
    assert "Téléchargement par lien" in q.edits[-1] and "cookies" in q.edits[-1].lower()
    assert await tools.ytdlp_options(svc.settings) == tools.js_runtime_args()

    # Cookies envoyés en fichier
    await click(ctx, "yta")

    class TgFile:
        async def download_as_bytearray(self):
            return bytearray(COOKIES)

    async def get_file():
        return TgFile()

    msg = FakeMessage("")
    msg.document = SimpleNamespace(file_name="cookies.txt", file_size=len(COOKIES), get_file=get_file)
    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=42),
                          effective_chat=SimpleNamespace(id=42), effective_message=msg)
    await handlers.on_file(upd, ctx)
    path = tools.cookies_path(svc.settings)
    assert msg.deleted and path.is_file() and oct(path.stat().st_mode & 0o777) == "0o600"
    assert any("Cookies enregistrés (3" in t for t in bot.sent)

    # Proxy saisi (message supprimé, valeur masquée à l'écran)
    await click(ctx, "ytp")
    m = await type_text(ctx, "http://moi:secret@proxy.example:8080")
    assert m.deleted
    opts = await tools.ytdlp_options(svc.settings)
    assert opts[-4:] == ["--cookies", str(path), "--proxy", "http://moi:secret@proxy.example:8080"]
    q = await click(ctx, "yt")
    assert "secret" not in q.edits[-1] and "•••@proxy.example" in q.edits[-1]

    # Test : yt-dlp factice qui affiche le titre
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
    assert edits and "YouTube répond" in edits[-1] and "Me at the zoo" in edits[-1]
    fake.write_text("#!/bin/sh\necho \"ERROR: Sign in to confirm you're not a bot\" >&2\nexit 1\n")
    await click(ctx, "ytt")
    assert "refuse" in edits[-1] and "datacenter" in edits[-1]

    await click(ctx, "ytx")
    await click(ctx, "ytpx")
    assert not path.exists() and await tools.ytdlp_options(svc.settings) == tools.js_runtime_args()


# ---------------------------------------------------------------- liste de proxies
def test_parse_proxy_list_formats():
    text = """# liste gratuite
1.2.3.4:8080
1.2.3.4:8080
user:pass@5.6.7.8:3128
9.9.9.9:1080:bob:secret
socks5://10.0.0.1:1080
n'importe quoi
proxy.example.com:80 extra-colonne
"""
    assert tools.parse_proxy_list(text) == [
        "http://1.2.3.4:8080", "http://user:pass@5.6.7.8:3128", "http://bob:secret@9.9.9.9:1080",
        "socks5://10.0.0.1:1080", "http://proxy.example.com:80",
    ]
    assert tools.parse_proxy_list("1.2.3.4:1080", "socks5_proxies.txt") == ["socks5://1.2.3.4:1080"]
    assert tools.parse_proxy_list("1.2.3.4:1080", "SOCKS4.TXT") == ["socks4://1.2.3.4:1080"]


@needs_ffmpeg
async def test_downloads_rotate_through_the_proxy_list(svc, tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    fake = tmp_path / "yt-dlp"
    # Ne réussit qu'avec le proxy « good » ; « gone » simule une vidéo supprimée (inutile de réessayer)
    fake.write_text(f"""#!/bin/sh
echo "$@" >> {log}
case "$*" in
  *gone*) echo "ERROR: Video unavailable" >&2; exit 1 ;;
  *http://good:8080*) echo "/tmp/ok.mp4"; exit 0 ;;
  *) echo "ERROR: Sign in to confirm you're not a bot" >&2; exit 1 ;;
esac
""")
    os.chmod(fake, 0o755)
    monkeypatch.setattr(tools, "ytdlp_command", lambda: [str(fake)])
    await tools.set_proxy_list(["http://bad1:8080", "http://bad2:8080", "http://good:8080"])

    res = await tools.run_ytdlp(svc.settings, ["--", "https://youtu.be/x"])
    assert res.ok and res.proxy == "http://good:8080" and res.attempts == 3
    assert "--socket-timeout" in log.read_text()
    assert (await tools.proxy_rotation())[0] == "http://good:8080"  # les proxies en échec sont mis de côté
    st = await tools.proxy_state()
    assert set(st["bad"]) == {"http://bad1:8080", "http://bad2:8080"} and st["ok"] == ["http://good:8080"]

    log.write_text("")
    res = await tools.run_ytdlp(svc.settings, ["--", "https://youtu.be/x"])
    assert res.ok and res.attempts == 1  # le dernier proxy qui a marché passe en premier

    res = await tools.run_ytdlp(svc.settings, ["--", "https://youtu.be/gone"])
    assert not res.ok and res.attempts == 1 and "unavailable" in res.error  # erreur définitive : pas de rotation

    # Un proxy unique réglé est prioritaire sur la liste
    from storyforge import db

    await db.kv_set(tools.PROXY_KEY, "http://good:8080")
    log.write_text("")
    res = await tools.run_ytdlp(svc.settings, ["--", "https://youtu.be/x"])
    assert res.ok and res.proxy is None and log.read_text().count("--proxy") == 1


@needs_ffmpeg
async def test_proxy_list_from_the_bot(svc, monkeypatch):
    import asyncio

    ctx, bot = make_ctx(svc)
    alive = {"socks5://1.1.1.1:1080", "socks5://3.3.3.3:1080"}

    async def check(url, timeout=10.0):
        return url in alive

    monkeypatch.setattr(tools, "check_proxy_reaches_youtube", check)
    await click(ctx, "ytl")
    assert ctx.user_data["await"] == {"kind": "ytdlp_proxies"}
    content = b"1.1.1.1:1080\n2.2.2.2:1080\n3.3.3.3:1080\n"

    class TgFile:
        async def download_as_bytearray(self):
            return bytearray(content)

    async def get_file():
        return TgFile()

    msg = FakeMessage("")
    msg.document = SimpleNamespace(file_name="socks5.txt", file_size=len(content), get_file=get_file)
    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=42),
                          effective_chat=SimpleNamespace(id=42), effective_message=msg)
    await handlers.on_file(upd, ctx)
    for _ in range(50):  # le test tourne en tâche de fond
        await asyncio.sleep(0.02)
        if (await tools.proxy_state())["tested_at"]:
            break
    st = await tools.proxy_state()
    assert st["list"] == ["socks5://1.1.1.1:1080", "socks5://2.2.2.2:1080", "socks5://3.3.3.3:1080"]
    assert st["ok"] == ["socks5://1.1.1.1:1080", "socks5://3.3.3.3:1080"]
    assert await tools.proxy_rotation() == ["socks5://1.1.1.1:1080", "socks5://3.3.3.3:1080"]
    assert any("3 proxies enregistrés" in t for t in bot.sent)
    q = await click(ctx, "yt")
    assert "3</b> adresses, 2 fonctionnelles" in q.edits[-1]

    # Liste collée dans le message « Proxy »
    await click(ctx, "ytp")
    await type_text(ctx, "4.4.4.4:8080\n5.5.5.5:8080")
    assert (await tools.proxy_state())["list"] == ["http://4.4.4.4:8080", "http://5.5.5.5:8080"]
    await click(ctx, "ytlx")
    for _ in range(20):  # le test lancé en fond se termine après le retrait
        await asyncio.sleep(0.02)
    assert (await tools.proxy_state())["list"] == []  # il ne remet pas l'ancienne liste
