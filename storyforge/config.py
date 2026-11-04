"""Configuration globale chargée depuis l'environnement / le fichier .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(NamedTuple):
    provider: str
    label: str
    base_url: str | None
    model: str
    api_key: str
    env: str  # nom de la variable de la clé dans le .env
    free: bool


LLM_PROVIDERS: dict[str, dict] = {
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "model": "gemini-flash-latest", "key": "gemini_api_key", "env": "GEMINI_API_KEY", "free": True},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1",
             "model": "llama-3.3-70b-versatile", "key": "groq_api_key", "env": "GROQ_API_KEY", "free": True},
    "openai": {"label": "OpenAI", "base_url": None,
               "model": "gpt-4.1", "key": "openai_api_key", "env": "OPENAI_API_KEY", "free": False},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram -----------------------------------------------------------
    telegram_bot_token: str = ""
    # IDs Telegram autorisés à piloter le système, séparés par des virgules.
    telegram_admin_ids: str = ""

    # --- IA qui écrit les histoires ------------------------------------------
    # auto | gemini | groq | openai — « auto » prend le premier fournisseur dont la clé est renseignée.
    llm_provider: str = "auto"
    llm_model: str = ""  # vide = modèle par défaut du fournisseur
    gemini_api_key: str = ""  # Google AI Studio (offre gratuite)
    groq_api_key: str = ""  # Groq (offre gratuite)
    openai_api_key: str = ""  # OpenAI (payant)
    openai_model: str = "gpt-4.1"
    openai_base_url: str | None = None  # API compatible OpenAI personnalisée
    openai_temperature: float = 0.95

    # --- ElevenLabs ---------------------------------------------------------
    elevenlabs_api_key: str = ""
    elevenlabs_api_keys: str = ""  # clés de secours, séparées par des virgules
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
    auto_update: bool = True  # lu par deploy/update.sh (AUTO_UPDATE=false pour suspendre)

    # --- YouTube (OAuth Google) --------------------------------------------
    google_client_secrets_file: Path = Path("secrets/google_client_secret.json")
    # Redirection "loopback" : le navigateur affiche une erreur, on colle l'URL dans Telegram.
    google_redirect_uri: str = "http://localhost:8765/"

    # --- TikTok (Content Posting API) --------------------------------------
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = ""

    # --- Instagram (API avec connexion Instagram) ----------------------------
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_redirect_uri: str = ""  # vide = <PUBLIC_URL>/oauth/instagram
    instagram_api_version: str = "v26.0"

    # --- Mini App Telegram / serveur web ------------------------------------
    webapp_enabled: bool = False
    # 127.0.0.1 : joignable seulement via le proxy HTTPS local (Caddy). Docker : 0.0.0.0 (voir Dockerfile).
    webapp_host: str = "127.0.0.1"
    webapp_port: int = 8080
    # Proxys autorisés à transmettre l'adresse IP réelle des visiteurs (X-Forwarded-For)
    webapp_trusted_proxies: str = "127.0.0.1"
    # URL publique HTTPS (ex: https://studio.mondomaine.com) — requise pour la Mini App
    # et la page de connexion des comptes YouTube / TikTok.
    public_url: str = ""
    # Contact affiché sur les pages légales (adresse e-mail ou lien), facultatif
    legal_contact: str = ""
    # Clé de signature des liens de connexion et des aperçus (générée par install.sh ;
    # à défaut, dérivée du token Telegram).
    secret_key: str = ""

    # ------------------------------------------------------------------------
    @property
    def llm(self) -> LLMConfig:
        """Fournisseur d'IA effectif pour l'écriture des histoires."""
        name = (self.llm_provider or "auto").lower()
        if name not in LLM_PROVIDERS:
            name = "auto"
        if name == "auto":
            name = next((n for n in ("gemini", "groq", "openai") if getattr(self, LLM_PROVIDERS[n]["key"])), "openai")
        preset = LLM_PROVIDERS[name]
        model = self.llm_model or (self.openai_model if name == "openai" else preset["model"])
        base_url = self.openai_base_url if name == "openai" and self.openai_base_url else preset["base_url"]
        return LLMConfig(name, preset["label"], base_url, model, getattr(self, preset["key"]), preset["env"], preset["free"])

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
