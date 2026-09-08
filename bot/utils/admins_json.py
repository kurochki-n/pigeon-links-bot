import asyncio
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class AdminStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = asyncio.Lock()

    async def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
            value = json.loads(data)
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    async def list_admins(self) -> list[dict[str, Any]]:
        async with self.lock:
            return await self._read_unlocked()

    async def is_admin(self, telegram_id: int) -> bool:
        return any(
            item.get("telegram_id") == telegram_id for item in await self.list_admins()
        )

    async def add_if_first(self, user: dict[str, Any]) -> bool:
        async with self.lock:
            admins = await self._read_unlocked()
            if admins:
                return False
            await self._write_unlocked([user])
            return True

    async def add(self, user: dict[str, Any]) -> bool:
        async with self.lock:
            admins = await self._read_unlocked()
            if any(a.get("telegram_id") == user["telegram_id"] for a in admins):
                return False
            admins.append(user)
            await self._write_unlocked(admins)
            return True

    async def remove(self, telegram_id: int) -> bool:
        async with self.lock:
            admins = await self._read_unlocked()
            if len(admins) <= 1 or not any(
                a.get("telegram_id") == telegram_id for a in admins
            ):
                return False
            await self._write_unlocked(
                [a for a in admins if a.get("telegram_id") != telegram_id]
            )
            return True

    async def _write_unlocked(self, admins: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def write() -> None:
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as tmp:
                json.dump(admins, tmp, ensure_ascii=False, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
                name = tmp.name
            os.replace(name, self.path)

        await asyncio.to_thread(write)


def telegram_user_data(user: Any) -> dict[str, Any]:
    return (
        {
            "telegram_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }
        if not user.username
        else {"telegram_id": user.id, "username": user.username}
    )


def admin_label(admin: dict[str, Any]) -> str:
    if admin.get("username"):
        return f"@{admin['username']}"
    return (
        " ".join(filter(None, [admin.get("first_name"), admin.get("last_name")]))
        or "Без имени"
    )
