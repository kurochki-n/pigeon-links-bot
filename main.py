import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from bot.database.session import create_database_schema, create_engine_and_session
from bot.handlers.admin.admin import router as admin_router
from bot.handlers.user.start import router as user_router
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.workspace import WorkspaceMiddleware
from bot.services.commands_service import setup_bot_commands
from bot.services.storage_service import FileStorageService
from config import settings

log = logging.getLogger(__name__)


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    log.exception("Unhandled update exception", exc_info=event.exception)
    update = event.update
    user = getattr(getattr(update, "message", None), "from_user", None) or getattr(
        getattr(update, "callback_query", None), "from_user", None
    )
    if user:
        try:
            await bot.send_message(
                user.id, "Произошла временная ошибка. Попробуйте ещё раз."
            )
        except TelegramAPIError:
            pass
    return True


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    Path("data").mkdir(exist_ok=True)
    storage = FileStorageService(settings.storage_path)
    await storage.ensure_root()
    engine, factory = create_engine_and_session(settings.database_url)
    await create_database_schema(engine)
    bot = Bot(
        settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    await setup_bot_commands(bot)

    dp = Dispatcher(storage=MemoryStorage())
    dp["storage"] = storage
    dp.update.outer_middleware(DatabaseMiddleware(factory))
    dp.update.outer_middleware(WorkspaceMiddleware())
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.errors.register(on_error)
    log.info("Bot starting")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        log.info("Bot stopping")
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
