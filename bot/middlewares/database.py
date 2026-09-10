import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.repositories.repositories import TelegramUserRepository, now

log = logging.getLogger(__name__)


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
            user = data.get("event_from_user") or getattr(event, "from_user", None)
            if user:
                await TelegramUserRepository(session).upsert(user, None, now())
            data["session"] = session
            data.setdefault("rollback_cleanups", [])
            data.setdefault("commit_cleanups", [])
            try:
                result = await handler(event, data)
                await session.commit()
                for cleanup in data["commit_cleanups"]:
                    try:
                        await cleanup()
                    except Exception:
                        log.exception("Committed file cleanup failed")
                return result
            except Exception:
                await session.rollback()
                for cleanup in reversed(data["rollback_cleanups"]):
                    try:
                        await cleanup()
                    except Exception:
                        log.exception("Rollback file cleanup failed")
                raise
