from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str
    telegram_channel_id: str = "@healthy_normal"
    telegram_admin_ids: list[int] = []

    # Database
    database_url: str
    database_url_sync: str

    # AI — text / vision
    text_model_provider: str = "openai"
    text_model_name: str = "gpt-4o-mini"
    vision_model_provider: str = "openai"
    vision_model_name: str = "gpt-4o"

    # AI — STT
    stt_provider: str = "openai"
    stt_model_name: str = "whisper-1"

    # OpenAI-compatible API (OpenAI or polza.ai or any compatible provider)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    webhook_url: str = ""
    webhook_path: str = "/webhook"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: str | list) -> list[int]:
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, str) and v.strip():
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return []

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_url)


settings = Settings()
