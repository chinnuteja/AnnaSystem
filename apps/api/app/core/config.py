from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[4]
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "foodleaf")
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://foodleaf:foodleaf@localhost:5432/foodleaf"
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    azure_openai_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_openai_api_key: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    azure_openai_api_version: str = os.getenv(
        "AZURE_OPENAI_API_VERSION", "2024-12-01-preview"
    )
    azure_openai_deployment: str = os.getenv(
        "AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini"
    )
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    google_cloud_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    google_cloud_location: str = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    sarvam_api_key: str = os.getenv("SARVAM_API_KEY", "")
    whatsapp_verify_token: str = os.getenv(
        "WHATSAPP_VERIFY_TOKEN", "foodleaf-local-verify"
    )
    whatsapp_access_token: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_app_secret: str = os.getenv("WHATSAPP_APP_SECRET", "")
    # Voice UX: text-first; optional Sarvam TTS follow-up; text-only ack on slow path by default.
    voice_ack_audio: bool = os.getenv("VOICE_ACK_AUDIO", "false").lower() in ("1", "true", "yes")
    voice_followup_tts: bool = os.getenv("VOICE_FOLLOWUP_TTS", "true").lower() in ("1", "true", "yes")


settings = Settings()
