from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"
    tenant: str = "default"

    database_url: str = "postgresql+asyncpg://twin:twin@localhost:5432/twin"
    redis_url: str = "redis://localhost:6379/0"

    blob_endpoint: str = "http://localhost:9000"
    blob_access_key: str = ""
    blob_secret_key: str = ""

    stt_api_key: str = ""
    llm_api_key: str = ""

    webhook_signing_secret: str = ""

    browser_profile_dir: str = "~/.config/twin-profile"
    browser_guest_profile_dir: str = "~/.config/twin-guest-profile"
    browser_headed: bool = True
    bot_display_name: str = "Fakery Guest"
    bot_locale: str = "id-ID"
    bot_timezone: str = "Asia/Jakarta"
    meeting_max_minutes: int = 45
    record_audio: bool = True
    blob_bucket: str = "fakery"
    profile_encryption_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
