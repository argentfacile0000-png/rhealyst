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
