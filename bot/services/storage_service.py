import asyncio
import mimetypes
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from aiogram import Bot

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class StoredFile:
    relative_path: str
    original_filename: str
    mime_type: str | None
    file_size: int | None


class FileStorageService:
    """Safe permanent filesystem storage; the database stores only relative paths."""

    def __init__(self, root: Path | str, max_file_size: int) -> None:
        if max_file_size <= 0:
            raise ValueError("max_file_size must be positive")
        self.root = Path(root).resolve()
        self.max_file_size = max_file_size

    async def ensure_root(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    def resolve_path(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError("Absolute paths are not allowed in storage")
        target = (self.root / relative).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Storage path escapes configured root") from exc
        return target

    def generate_unique_name(self, original_filename: str) -> str:
        safe = _SAFE_NAME.sub("_", Path(original_filename).name).strip("._")
        suffix = Path(safe).suffix[:16] if safe else ""
        stem = Path(safe).stem[:80] if safe else "file"
        return f"{uuid.uuid4().hex}_{stem}{suffix}"

    async def save_telegram_file(
        self,
        bot: Bot,
        file_id: str,
        category: str,
        original_filename: str,
        mime_type: str | None = None,
    ) -> StoredFile:
        await self.ensure_root()
        filename = self.generate_unique_name(original_filename)
        relative_path = Path(category) / filename
        destination = self.resolve_path(relative_path.as_posix())
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        try:
            await bot.download(file_id, destination=destination)
            size = (await asyncio.to_thread(destination.stat)).st_size
            if size > self.max_file_size:
                raise ValueError("Telegram file exceeds the configured size limit")
            return StoredFile(
                relative_path=relative_path.as_posix(),
                original_filename=Path(original_filename).name or "file",
                mime_type=mime_type or mimetypes.guess_type(original_filename)[0],
                file_size=size,
            )
        except Exception:
            await self.delete(relative_path.as_posix())
            raise

    async def delete(self, relative_path: str | None) -> None:
        if not relative_path:
            return
        try:
            path = self.resolve_path(relative_path)
        except ValueError:
            return
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def exists(self, relative_path: str | None) -> bool:
        if not relative_path:
            return False
        try:
            return await asyncio.to_thread(self.resolve_path(relative_path).is_file)
        except ValueError:
            return False

    def open(self, relative_path: str, mode: str = "rb") -> BinaryIO:
        return self.resolve_path(relative_path).open(mode)
