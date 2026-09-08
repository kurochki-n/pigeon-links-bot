from typing import Any

from aiogram.filters import Filter

from bot.utils.admins_json import AdminStore


class IsAdmin(Filter):
    async def __call__(self, event: Any, admin_store: AdminStore) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and await admin_store.is_admin(user.id))
