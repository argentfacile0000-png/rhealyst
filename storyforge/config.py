"""Configuration globale chargée depuis l'environnement / le fichier .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram -----------------------------------------------------------
    telegram_bot_token: str = ""
    # IDs Telegram autorisés à piloter le système, séparés par des virgules.
    telegram_admin_ids: str = ""

    # --- OpenAI (ChatGPT) ---------------------------------------------------
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    openai_base_url: str | None = None
    openai_temperature: float = 0.95

    # --- ElevenLabs ---------------------------------------------------------
    elevenlabs_api_key: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    # Voix par défaut des nouvelles chaînes (Adam, voix "premade").
    elevenlabs_default_voice_id: str = "pNInz6obpgDQGcFmaJgB"
    elevenlabs_default_voice_name: str = "Adam"

    # --- Stockage -----------------------------------------------------------
    data_dir: Path = Path("data")
    fonts_dir: Path = Path("assets/fonts")
    database_url: str = ""  # vide = SQLite dans data_dir

    # --- Rendu vidéo --------------------------------------------------------
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    video_width: int = 1080
    video_height: int = 1920
    video_fps: int = 30
    video_crf: int = 20
    video_preset: str = "veryfast"
    max_concurrent_renders: int = 1
    keep_videos_days: int = 7

    # --- Planning -----------------------------------------------------------
    timezone: str = "Europe/Paris"
    refill_interval_minutes: int = 5

    # --- YouTube (OAuth Google) --------------------------------------------
    google_client_secrets_file: Path = Path("secrets/google_client_secret.json")
    # Redirection "loopback" : le navigateur affiche une erreur, on colle l'URL dans Telegram.
    google_redirect_uri: str = "http://localhost:8765/"

    # --- TikTok (Content Posting API) --------------------------------------
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = ""

    # --- Mini App Telegram / serveur web ------------------------------------
    webapp_enabled: bool = False
    webapp_host: str = "0.0.0.0"
    webapp_port: int = 8080
    # URL publique HTTPS (ex: https://studio.mondomaine.com) — requise pour la Mini App
    # et la page de connexion des comptes YouTube / TikTok.
    public_url: str = ""
    # Clé de signature des liens de connexion (par défaut dérivée du token Telegram).
    secret_key: str = ""

    # ------------------------------------------------------------------------
    @property
    def admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for chunk in self.telegram_admin_ids.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.lstrip("-").isdigit():
                ids.add(int(chunk))
        return ids

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'storyforge.db').as_posix()}"

    @property
    def backgrounds_dir(self) -> Path:
        return self.data_dir / "backgrounds"

    @property
    def music_dir(self) -> Path:
        return self.data_dir / "music"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def public_base(self) -> str:
        return self.public_url.rstrip("/")

    @property
    def webapp_url(self) -> str | None:
        if self.webapp_enabled and self.public_base.startswith("https://"):
            return f"{self.public_base}/app"
        return None

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.backgrounds_dir, self.music_dir, self.output_dir, self.work_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
