from __future__ import annotations

import array
import hashlib
import hmac
import json
import random
import time
import urllib.parse
import wave
from pathlib import Path

import pytest

from storyforge.scheduler import parse_times
from storyforge.story.generator import (
    StoryGenerator,
    clean_narration,
    count_words,
    first_sentence,
    words_for_duration,
)
from storyforge.story.styles import FORMATS, random_dna
from storyforge.tts.elevenlabs import split_for_tts
from storyforge.tts.timing import Word, compute_keep_segments, remap_time, tighten_pauses, words_from_alignment
from storyforge.video.background import choose_start
from storyforge.video.captions import CaptionStyle, ass_color, ass_time, build_ass, display_words, hook_end_time
from storyforge.webapp.auth import validate_init_data

from .conftest import STORY_JSON, fake_openai


# ---------------------------------------------------------------- timing
def test_words_from_alignment_groups_characters():
    text = "Salut toi. Ok"
    starts = [i * 0.1 for i in range(len(text))]
    ends = [s + 0.08 for s in starts]
    words = words_from_alignment(list(text), starts, ends, offset=1.0)
    assert [w.text for w in words] == ["Salut", "toi.", "Ok"]
    assert words[0].start == pytest.approx(1.0)
    assert words[1].start == pytest.approx(1.6)
    assert words[-1].end == pytest.approx(1.0 + 1.2 + 0.08)


def _write_wav(path: Path, segments: list[tuple[float, bool]], sr: int = 16000) -> None:
    data = array.array("h")
    for dur, loud in segments:
        n = int(dur * sr)
        data.extend([8000 if (loud and i % 20 < 10) else 0 for i in range(n)])
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data.tobytes())


def test_tighten_pauses_shortens_silences_and_remaps(tmp_path: Path):
    src = tmp_path / "in.wav"
    # 0.5 silence, mot 0.5-1.0, 1.0 silence, mot 2.0-2.5, 0.5 silence
    _write_wav(src, [(0.5, False), (0.5, True), (1.0, False), (0.5, True), (0.5, False)])
    words = [Word("un", 0.5, 1.0), Word("deux", 2.0, 2.5)]
    out = tmp_path / "out.wav"
    new = tighten_pauses(src, out, words, max_pause=0.2, lead=0.05, tail=0.2, crossfade_ms=5)
    with wave.open(str(out)) as wf:
        dur = wf.getnframes() / wf.getframerate()
    assert dur < 1.6  # 3.0 s -> ~1.45 s
    assert new[0].start == pytest.approx(0.05, abs=0.01)
    gap = new[1].start - new[0].end
    assert gap == pytest.approx(0.2, abs=0.02)
    assert new[1].end <= dur + 0.01


def test_remap_time_default_offsets():
    keep = [(0.0, 1.0), (2.0, 3.0)]
    assert remap_time(0.5, keep) == pytest.approx(0.5)
    assert remap_time(2.5, keep) == pytest.approx(1.5)
    assert compute_keep_segments([], 3.0, max_pause=0.2, lead=0, tail=0) == [(0.0, 3.0)]


def test_split_for_tts_respects_limit():
    text = " ".join(["Une phrase assez longue pour le test."] * 200)
    chunks = split_for_tts(text, max_chars=500)
    assert all(len(c) <= 500 for c in chunks)
    assert " ".join(chunks) == " ".join(text.split())


# ---------------------------------------------------------------- fond aléatoire
def test_choose_start_fits_inside_source():
    rng = random.Random(1)
    for _ in range(200):
        start = choose_start(43 * 60, 98.0, rng=rng)
        assert 0 <= start and start + 98.0 <= 43 * 60


def test_choose_start_avoids_recent_segments():
    rng = random.Random(3)
    avoid = [(0, 500), (600, 1200)]
    for _ in range(50):
        start = choose_start(1300, 60, avoid=avoid, rng=rng)
        assert not any(min(start + 60, b) - max(start, a) > 0 for a, b in avoid)


def test_choose_start_short_source():
    assert choose_start(30, 60) == 0.0


# ---------------------------------------------------------------- sous-titres
def test_ass_helpers():
    assert ass_color("#FF8000") == "&H000080FF"
    assert ass_time(3725.456) == "1:02:05.46"


def test_display_words_merges_lone_punctuation():
    words = [Word("Quoi", 0, 0.3), Word("?", 0.3, 0.4), Word("«Non»,", 0.5, 0.8)]
    cues = display_words(words)
    assert [c.text for c in cues] == ["Quoi", "Non"]
    assert cues[0].end == pytest.approx(0.4)


def test_build_ass_pop_and_label():
    words = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate("Mon coloc a vendu x{y}z canapé".split())]
    ass = build_ass(words, style=CaptionStyle(mode="pop"), label="PARTIE 1", duration=3.0)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == 7  # 6 mots + label
    assert "PARTIE 1" in ass and "x(y)z" in ass and "x{y}z" not in ass


def test_build_ass_trio_and_captions_from():
    words = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate("a b c d e f".split())]
    ass = build_ass(words, style=CaptionStyle(mode="trio"), captions_from=0.6)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == 4  # mots c..f
    assert hook_end_time(words, "a b c.") == pytest.approx(0.85)


# ---------------------------------------------------------------- histoires
def test_clean_narration():
    t = clean_narration("Part 1: **Wow** c'est fou 😱 #storytime [rires] ok")
    assert "Part" not in t and "*" not in t and "#" not in t and "😱" not in t and "[rires]" not in t
    assert t.startswith("Wow c'est fou")


def test_words_and_sentence_helpers():
    assert count_words("Pas un message. Pas un appel !") == 6
    assert first_sentence("Quoi ? Oui.") == "Quoi ?"
    assert 150 < words_for_duration(60, "en", 1.1, 1.2) < 260


def test_dna_and_formats():
    dna = random_dna("female", random.Random(0))
    assert "woman" in dna.narrator
    assert {"askreddit", "drama", "revenge", "aita"} <= set(FORMATS)


async def test_generator_normalizes_parts(settings):
    client = fake_openai([STORY_JSON])
    gen = StoryGenerator(settings, client=client)
    story = await gen.generate(language="fr", formats=["drama"], parts=3, seconds_per_part=10, avoid_titles=["x"])
    assert len(story.parts) == 3
    assert story.hashtags == ["storytime", "coloc", "histoirevraie"]
    assert story.hook.startswith("Mon coloc a vendu")
    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert "French" in call["messages"][1]["content"]


async def test_generator_length_retry(settings):
    short_story = dict(STORY_JSON, parts=["Trop court."])
    long_story = dict(STORY_JSON, parts=[" ".join(["mot"] * 180) + "."])
    client = fake_openai([short_story, long_story])
    gen = StoryGenerator(settings, client=client)
    story = await gen.generate(language="en", parts=1, seconds_per_part=60, voice_speed=1.0, speedup=1.0)
    assert len(client.chat.completions.calls) == 2
    assert story.word_count > 100


def test_reasoning_model_params(settings):
    settings.openai_model = "gpt-5-mini"
    from storyforge.story.generator import _is_reasoning_model

    assert _is_reasoning_model("gpt-5-mini") and not _is_reasoning_model("gpt-4.1")


# ---------------------------------------------------------------- planning
def test_parse_times():
    assert parse_times("9h, 13:30 19:00 9:00") == ["09:00", "13:30", "19:00"]
    with pytest.raises(ValueError):
        parse_times("25:00")


# ---------------------------------------------------------------- Mini App
def _init_data(token: str, user_id: int, age: int = 0) -> str:
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAE",
        "user": json.dumps({"id": user_id, "first_name": "Ana"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def test_validate_init_data():
    token = "123456:TEST-token"
    good = _init_data(token, 42)
    assert validate_init_data(good, token)["id"] == 42
    assert validate_init_data(good, "999:other") is None
    assert validate_init_data(good.replace("Ana", "Bob"), token) is None
    assert validate_init_data(_init_data(token, 42, age=200000), token) is None


# ---------------------------------------------------------------- fournisseurs d'IA
def test_llm_provider_resolution(settings):
    settings.openai_api_key = ""
    assert settings.llm.provider == "openai" and not settings.llm.api_key
    settings.groq_api_key = "q"
    assert settings.llm.provider == "groq" and settings.llm.base_url == "https://api.groq.com/openai/v1"
    settings.gemini_api_key = "g"
    llm = settings.llm
    assert llm.provider == "gemini" and llm.free and llm.model == "gemini-flash-latest"
    assert llm.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    settings.llm_provider = "groq"
    settings.llm_model = "openai/gpt-oss-120b"
    assert settings.llm.provider == "groq" and settings.llm.model == "openai/gpt-oss-120b"


class _FlakyCompletions:
    """Refuse le premier appel (paramètre non supporté), puis répond."""

    def __init__(self, payload, error):
        self.payload, self.error, self.calls = payload, error, []

    async def create(self, **kwargs):
        from types import SimpleNamespace

        self.calls.append(kwargs)
        if self.error and len(self.calls) == 1:
            raise self.error
        msg = SimpleNamespace(content="```json\n" + json.dumps(self.payload) + "\n```")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20))


def _api_error(cls, status):
    import httpx

    return cls("erreur", response=httpx.Response(status, request=httpx.Request("POST", "https://x")), body=None)


async def test_gemini_params_and_fallback(settings):
    import openai
    from types import SimpleNamespace

    settings.openai_api_key = ""
    settings.gemini_api_key = "g"
    comp = _FlakyCompletions(STORY_JSON, _api_error(openai.BadRequestError, 400))
    gen = StoryGenerator(settings, client=SimpleNamespace(chat=SimpleNamespace(completions=comp)))
    story = await gen.generate(language="fr", parts=2, seconds_per_part=10)
    first, retry = comp.calls[0], comp.calls[1]
    assert first["model"] == "gemini-flash-latest" and first["max_tokens"] == 16384 and first["reasoning_effort"] == "low"
    assert "presence_penalty" not in first and "max_completion_tokens" not in first
    assert set(retry) == {"model", "messages"}  # nouvel essai minimal
    assert story.model == "gemini-flash-latest" and len(story.parts) == 2


async def test_free_tier_limit_message(settings):
    import openai
    from types import SimpleNamespace

    from storyforge.story import StoryError

    settings.openai_api_key = ""
    settings.groq_api_key = "q"
    comp = _FlakyCompletions(STORY_JSON, _api_error(openai.RateLimitError, 429))
    gen = StoryGenerator(settings, client=SimpleNamespace(chat=SimpleNamespace(completions=comp)))
    with pytest.raises(StoryError, match="Limite de Groq atteinte.*gratuite"):
        await gen.generate(language="fr", parts=1, seconds_per_part=10)
