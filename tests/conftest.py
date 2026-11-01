from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from storyforge.config import Settings
from storyforge.core import Services
from storyforge.tts import TTSResult, Word

ROOT = Path(__file__).resolve().parents[1]
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg requis")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        fonts_dir=ROOT / "assets" / "fonts",
        telegram_bot_token="123456:TEST-token",
        telegram_admin_ids="42",
        openai_api_key="sk-test",
        elevenlabs_api_key="el-test",
        video_width=540,
        video_height=960,
        video_preset="ultrafast",
        video_crf=32,
        timezone="Europe/Paris",
    )
    s.ensure_dirs()
    return s


def make_video(path: Path, seconds: float, size: str = "640x360") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30", "-f", "lavfi",
         "-i", "sine=frequency=300", "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


STORY_JSON = {
    "title": "Mon coloc a vendu mon canapé pendant mes vacances",
    "parts": [
        "Mon coloc a vendu mon canapé pendant mes vacances. J'avais 24 ans et je bossais comme barista. "
        "Pas un message. Pas un appel. Juste une photo de mon salon vide.",
        "Il m'a dit, tu t'en servais jamais. Alors j'ai vendu sa console. Il a hurlé comme une alarme de voiture.",
    ],
    "youtube_title": "Il a vendu MON canapé 😳",
    "description": "Il pensait que je ne dirais rien.\nIl avait tort.",
    "hashtags": ["storytime", "#coloc", "histoire vraie"],
    "tiktok_caption": "Il a vendu mon canapé… #storytime #coloc",
}


class FakeCompletions:
    def __init__(self, payloads: list[dict]):
        self.payloads = payloads
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        msg = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200))


def fake_openai(payloads: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(payloads)))


async def fake_synthesize(text, voice_id, settings, workdir: Path, *, speedup=1.0, max_pause=0.22):
    workdir.mkdir(parents=True, exist_ok=True)
    tokens = text.split()
    words, t = [], 0.1
    for tok in tokens:
        words.append(Word(tok, t, t + 0.2))
        t += 0.25
    dur = t + 0.3
    wav = workdir / "narration.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220", "-t", f"{dur:.2f}",
                    "-ac", "1", "-ar", "44100", str(wav)], check=True)
    return TTSResult(audio_path=wav, words=words, duration=dur, characters=len(text))


@pytest.fixture
async def svc(settings):
    make_video(settings.backgrounds_dir / "cooking" / "long.mp4", 40)
    make_video(settings.backgrounds_dir / "minecraft" / "short.mp4", 3)
    s = Services(settings)
    s.pipeline.stories._client = fake_openai([STORY_JSON])
    s.pipeline.tts.synthesize = fake_synthesize
    await s.start()
    yield s
    await s.stop()
