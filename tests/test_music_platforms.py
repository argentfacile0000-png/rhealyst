"""Bande son (banque, réglages par chaîne, mixage) et configuration des plateformes depuis le bot."""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from storyforge import pipeline as pipeline_mod
from storyforge.bot import handlers
from storyforge.platform_apps import PlatformApps, parse_google_client
from storyforge.publishers import PublisherError, tiktok
from storyforge.webapp.server import create_app

from .conftest import needs_ffmpeg
from .test_bot import FakeMessage, click, make_ctx, type_text

pytestmark = needs_ffmpeg
PUBLIC = "https://studio.example.com"


def make_track(path: Path, seconds: float, freq: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}", "-t", str(seconds),
                    "-c:a", "aac", str(path)], check=True)
    return path


# ---------------------------------------------------------------- bande son
async def test_music_pick_random_start_and_selection(svc):
    music = svc.pipeline.music
    make_track(svc.settings.music_dir / "lofi.m4a", 30)
    make_track(svc.settings.music_dir / "court.m4a", 3, 660)
    assert music.names() == ["court.m4a", "lofi.m4a"]
    starts = set()
    for seed in range(8):
        pick = await music.pick(["lofi.m4a"], 10, random.Random(seed))
        assert pick.path.name == "lofi.m4a" and 0 <= pick.start <= 20
        starts.add(pick.start)
    assert len(starts) > 1  # départ au hasard
    pick = await music.pick(["court.m4a"], 10, random.Random(1))
    assert pick.path.name == "court.m4a" and pick.start == 0  # trop courte : bouclée depuis le début
    pick = await music.pick(["supprimée.mp3"], 10, random.Random(1))
    assert pick is not None  # sélection disparue : toute la banque
    assert music.resolve("../storyforge.db") is None


async def test_video_is_mixed_with_the_channel_soundtrack(svc, monkeypatch):
    make_track(svc.settings.music_dir / "lofi.m4a", 30)
    make_track(svc.settings.music_dir / "piano.m4a", 30, 523)
    jobs = []
    real_render = pipeline_mod.render

    async def spy(job, settings, on_progress=None):
        jobs.append(job)
        return await real_render(job, settings, on_progress=on_progress)

    monkeypatch.setattr(pipeline_mod, "render", spy)
    ch = await svc.create_channel("Musique", approval_required=True, min_seconds=5, max_seconds=6,
                                  music_volume=0.12, music_tracks=["piano.m4a"])
    [vid] = await svc.pipeline.generate(ch.id)
    job = jobs[-1]
    assert job.music.name == "piano.m4a" and job.music_volume == 0.12 and 0 <= job.music_start <= 25
    v = await svc.get_video(vid)
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name",
                          "-of", "csv=p=0", v.file_path], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "aac"

    # Bande son coupée : aucune piste
    await svc.update_channel(ch.id, music_volume=0.0)
    await svc.pipeline.generate(ch.id)
    assert jobs[-1].music is None


async def test_music_bank_from_the_bot(svc, tmp_path):
    ctx, bot = make_ctx(svc)
    ch = await svc.create_channel("Lofi")
    q = await click(ctx, "mu")
    assert "Bande son" in q.edits[-1] and "Aucune piste" in q.edits[-1]
    await click(ctx, "mua")
    assert ctx.user_data["await"] == {"kind": "music_add"}

    # Envoi d'un fichier audio
    src = make_track(tmp_path / "Ma Piste (lofi).mp3".replace(".mp3", ".m4a"), 12)
    replies = []

    class TgFile:
        async def download_to_drive(self, custom_path):
            shutil.copy(src, custom_path)

    async def get_file():
        return TgFile()

    msg = FakeMessage("")
    msg.audio = SimpleNamespace(file_name="Ma Piste (lofi).m4a", file_unique_id="u1", file_size=src.stat().st_size,
                                get_file=get_file)

    async def reply_text(text, **kw):
        replies.append(text)

        async def edit(t, **kw):
            replies.append(t)
        return SimpleNamespace(edit_text=edit)

    msg.reply_text = reply_text
    upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=42),
                          effective_chat=SimpleNamespace(id=42), effective_message=msg)
    await handlers.on_file(upd, ctx)
    assert any("Piste ajoutée" in r for r in replies), replies
    names = svc.pipeline.music.names()
    assert len(names) == 1 and names[0].endswith(".m4a") and "/" not in names[0]

    # Activer partout, choisir la piste pour la chaîne, puis la supprimer
    await click(ctx, "mue")
    assert (await svc.get_channel(ch.id)).music_volume == 0.08
    await click(ctx, f"cm:{ch.id}:music_tracks:0")
    assert (await svc.get_channel(ch.id)).music_tracks == names
    q = await click(ctx, "mut:0")
    assert "Lofi" in q.edits[-1]
    await click(ctx, "mud:0")
    await click(ctx, "muD:0")
    assert svc.pipeline.music.names() == [] and (await svc.get_channel(ch.id)).music_tracks == []

    # Format non pris en charge
    await click(ctx, "mua")
    bad = FakeMessage("")
    bad.document = SimpleNamespace(file_name="notes.pdf", file_unique_id="u2", file_size=10, get_file=get_file)
    upd.effective_message = bad
    await handlers.on_file(upd, ctx)
    assert "Format non pris en charge" in bad.replies[-1]


# ---------------------------------------------------------------- plateformes
@pytest.fixture
def public(svc):
    svc.settings.webapp_enabled = True
    svc.settings.public_url = PUBLIC
    return svc


async def test_tiktok_and_instagram_keys_from_the_bot(public, monkeypatch):
    svc = public
    ch = await svc.create_channel("Plateformes")
    assert "app TikTok" in svc.platform_problem("tiktok", ch.id)
    ctx, bot = make_ctx(svc)
    q = await click(ctx, "pf")
    assert "Plateformes" in q.edits[-1] and PUBLIC in q.edits[-1]
    q = await click(ctx, "pft")
    assert f"{PUBLIC}/oauth/tiktok" in q.edits[-1] and f"{PUBLIC}/legal/privacy" in q.edits[-1]

    calls = []

    async def check(key, secret):
        calls.append((key, secret))
        if secret.startswith("bad"):
            raise PublisherError("OAuth TikTok refusé : invalid client")
        return True

    monkeypatch.setattr(tiktok, "check_client", check)
    await click(ctx, "pfk:tiktok")
    m1 = await type_text(ctx, "awx123clientkey")
    assert m1.deleted and any("2/2" in t for t in bot.sent)
    m2 = await type_text(ctx, "bad_secret_aaaaaaaaaaaa")
    assert m2.deleted and "invalid client" in m2.replies[-1]
    assert ctx.user_data["await"]["values"] == {}  # on recommence depuis le Client key
    await type_text(ctx, "awx123clientkey")
    await type_text(ctx, "good_secret_bbbbbbbbbbbb")
    assert svc.settings.tiktok_client_key == "awx123clientkey" and svc.platform_problem("tiktok", ch.id) is None
    assert any("App TikTok enregistrée" in t for t in bot.sent)

    # Les identifiants survivent à un redémarrage
    svc.settings.tiktok_client_key = ""
    await PlatformApps(svc.settings).load()
    assert svc.settings.tiktok_client_key == "awx123clientkey"

    # Valeur au mauvais format
    await click(ctx, "pfk:instagram")
    m = await type_text(ctx, "pas-un-id")
    assert "chiffres" in m.replies[-1]
    await type_text(ctx, "123456789012345")
    await type_text(ctx, "0123456789abcdef0123456789abcdef")
    assert svc.platform_problem("instagram", ch.id) is None

    # Retrait : on revient à la configuration du .env (vide ici)
    await click(ctx, "pfx:tiktok")
    assert svc.settings.tiktok_client_key == "" and svc.platform_problem("tiktok", ch.id)


def google_json(kind="web", redirect=f"{PUBLIC}/oauth/youtube") -> bytes:
    return json.dumps({kind: {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-x",
                              "redirect_uris": [redirect]}}).encode()


async def test_google_client_from_the_bot(public, tmp_path):
    svc = public
    svc.settings.google_client_secrets_file = tmp_path / "secrets" / "google_client_secret.json"
    ch = await svc.create_channel("YouTube")
    assert "client OAuth Google" in svc.platform_problem("youtube", ch.id)
    ctx, bot = make_ctx(svc)
    await click(ctx, "pfg")
    assert ctx.user_data["await"] == {"kind": "google_json"}

    async def send(raw: bytes) -> FakeMessage:
        class TgFile:
            async def download_as_bytearray(self):
                return bytearray(raw)

        async def get_file():
            return TgFile()

        msg = FakeMessage("")
        msg.document = SimpleNamespace(file_name="client.json", file_size=len(raw), get_file=get_file)
        upd = SimpleNamespace(callback_query=None, effective_user=SimpleNamespace(id=42),
                              effective_chat=SimpleNamespace(id=42), effective_message=msg)
        await handlers.on_file(upd, ctx)
        return msg

    m = await send(b'{"type": "service_account"}')
    assert m.deleted and any("pas un client OAuth" in t for t in bot.sent)
    assert not svc.settings.google_client_secrets_file.exists()
    await send(google_json(redirect="https://ailleurs/oauth"))
    path = svc.settings.google_client_secrets_file
    assert path.exists() and oct(path.stat().st_mode & 0o777) == "0o600"
    assert any("Client OAuth Google enregistré" in t and "/oauth/youtube" in t for t in bot.sent)
    assert svc.platform_problem("youtube", ch.id) is None and svc.oauth_is_automatic("youtube", ch.id)
    q = await click(ctx, "pf")
    assert "client Application Web" in q.edits[-1]
    await click(ctx, "pfx:youtube")
    assert not path.exists()


def test_parse_google_client():
    assert parse_google_client(google_json("installed"))[0] == "installed"
    with pytest.raises(ValueError):
        parse_google_client(b"not json")
    with pytest.raises(ValueError):
        parse_google_client(json.dumps({"web": {"client_id": "x", "client_secret": "y"}}).encode())


async def test_legal_pages_and_portal_hint(public):
    svc = public
    svc.settings.legal_contact = "moi@example.com"
    client = TestClient(create_app(svc))
    for kind, needle in (("privacy", "Privacy Policy"), ("terms", "Terms of Service"), ("data-deletion", "Data deletion")):
        r = client.get(f"/legal/{kind}")
        assert r.status_code == 200 and needle in r.text and "mailto:moi@example.com" in r.text
        assert "script-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/legal/autre").status_code == 404
    home = client.get("/")
    assert home.status_code == 200 and "/legal/privacy" in home.text
    ch = await svc.create_channel("Portail")
    page = client.get(f"/connect/{svc.portal_token(ch.id)}").text
    assert "README" not in page and "Plateformes" in page
