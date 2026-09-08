from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str = "sqlite+aiosqlite:///./data/database.db"
    log_level: str = "INFO"
    max_github_archive_size: int = 50 * 1024 * 1024
    storage_path: Path = Path("./data/storage")

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def data_dir(self) -> Path:
        return Path("data")


settings = Settings()
