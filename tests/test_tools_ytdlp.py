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
