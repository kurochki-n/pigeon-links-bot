from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.repositories.repositories import TelegramUserRepository, now


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        async with self.factory() as session:
            user = getattr(event, "from_user", None)
            if user:
                await TelegramUserRepository(session).upsert(user, None, now())
            data["session"] = session
            data.setdefault("rollback_cleanups", [])
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                for cleanup in data["rollback_cleanups"]:
                    await cleanup()
                raise
