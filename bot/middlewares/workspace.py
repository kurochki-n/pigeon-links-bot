from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware

from bot.workspace import workspace_owner_id


class WorkspaceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user") or getattr(event, "from_user", None)
        token = workspace_owner_id.set(user.id if user else None)
        try:
            return await handler(event, data)
        finally:
            workspace_owner_id.reset(token)
