from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./data/database.db"
    log_level: str = "INFO"
    max_telegram_file_size: int = Field(default=20 * 1024 * 1024, gt=0)
    storage_path: Path = Path("./data/storage")
    delivery_token_key_path: Path = Path("./data/delivery_tokens.key")

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def data_dir(self) -> Path:
        return Path("data")


settings = Settings()
