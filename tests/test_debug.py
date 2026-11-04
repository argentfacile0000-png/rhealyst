"""Mode débogage : génération étape par étape, journaux et fichiers, enregistrement final."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from storyforge.models import VideoStatus

from .conftest import needs_ffmpeg
from .test_bot import click, make_ctx

pytestmark = needs_ffmpeg


async def test_debug_mode_step_by_step(svc, monkeypatch):
    ch = await svc.create_channel("Debug", min_seconds=5, max_seconds=6, hook_card=True)
    ctx, bot = make_ctx(svc)
    sent = []
    messages = []

    async def capture(kind):
        async def fn(chat_id, *a, caption=None, filename=None, **kw):
            sent.append((kind, caption or filename))
        return fn

    for kind in ("photo", "video", "audio", "document"):
        monkeypatch.setattr(bot, f"send_{kind}", await capture(kind), raising=False)

    async def send_message(chat_id, text, **kw):
        messages.append(text)
        return SimpleNamespace(message_id=1, edit_text=lambda *a, **k: None)

    monkeypatch.setattr(bot, "send_message", send_message)

    q = await click(ctx, "dbg")
    assert "Mode débogage" in q.edits[-1] and "Quelle chaîne" in q.edits[-1]
    q = await click(ctx, f"dbgc:{ch.id}:v")
    assert "○  Histoire" in q.edits[-1]
    sess = ctx.user_data["debug"]

    async def step(key):
        await click(ctx, f"dbgr:{key}")
        for _ in range(400):
            await asyncio.sleep(0.05)
            if not sess.busy and sess.steps[key].status != "todo":
                break
        assert sess.steps[key].status == "ok", sess.steps[key].lines

    # Impossible de sauter une étape
    q = await click(ctx, "dbgr:voice")
    assert q.answers[-1] == "Termine d'abord les étapes précédentes"

    await step("story")
    assert any("Appel 1 : modèle" in line for line in sess.steps["story"].lines)
    assert ("document", "ia_prompts_et_reponses.txt") in sent and ("document", "histoire.json") in sent
    await step("voice")
    assert ("audio", "voix.mp3") in sent
    await step("background")
    assert ("photo", "fond.jpg") in sent
    await step("captions")
    assert ("document", "sous-titres.ass") in sent and ("photo", "carte_accroche.png") in sent
    await step("render")
    assert ("video", "video.mp4") in sent
    assert "▶ Enregistrement" in str(messages[-1]) or "Montage" in messages[-1]

    # Refaire une étape invalide les suivantes
    await step("background")
    assert sess.steps["captions"].status == "todo" and sess.steps["render"].status == "todo"
    await step("captions")
    await step("render")
    await step("save")
    v = await svc.get_video(sess.video_id)
    assert v.status == VideoStatus.PENDING_APPROVAL and v.file_path

    # Fichiers renvoyés à la demande, puis arrêt
    sent.clear()
    await click(ctx, "dbgf:story")
    assert ("document", "histoire.json") in sent
    workdir = sess.workdir
    await click(ctx, "dbgx")
    assert "debug" not in ctx.user_data and not workdir.exists()


async def test_debug_story_error_is_logged_with_raw_reply(svc):
    from storyforge.debug import DebugSession, run_step

    ch = await svc.create_channel("Erreur")

    async def create(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="pas du JSON"))],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))

    svc.pipeline.stories._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    sess = DebugSession(ch.id)
    log = await run_step(svc, sess, "story")
    assert log.status == "error" and any("ERREUR" in line for line in log.lines)
    names = [n for n, _ in log.files]
    assert "ia_prompts_et_reponses.txt" in names and "erreur.txt" in names
    raw = dict(log.files)["ia_prompts_et_reponses.txt"].decode()
    assert "RÉPONSE BRUTE" in raw and "pas du JSON" in raw and "SYSTEM" in raw
    sess.cleanup()
