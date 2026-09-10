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
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.workspace import WorkspaceMiddleware
from bot.services.commands_service import setup_bot_commands
from bot.services.delivery_bot_manager import DeliveryBotManager
from bot.services.storage_service import FileStorageService
from bot.services.token_cipher import TokenCipher
from config import settings

log = logging.getLogger(__name__)


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    exception = event.exception
    log.error(
        "Unhandled update exception",
        exc_info=(type(exception), exception, exception.__traceback__),
    )
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
    storage = FileStorageService(settings.storage_path, settings.max_telegram_file_size)
    await storage.ensure_root()
    engine, factory = create_engine_and_session(settings.database_url)
    await create_database_schema(engine)
    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )
    me = await bot.get_me()
    attribution = "Оригинальный бот: @PigeonLinksBot"
    try:
        if (me.username or "").casefold() != "pigeonlinksbot":
            await bot.set_my_short_description(short_description=attribution)
        else:
            current = await bot.get_my_short_description()
            if current.short_description == attribution:
                await bot.set_my_short_description(short_description="")
    except TelegramAPIError as exc:
        log.warning("Could not update attribution short description: %s", exc)
    await setup_bot_commands(bot)

    token_cipher = TokenCipher(settings.delivery_token_key_path)
    delivery_manager = DeliveryBotManager(factory, storage, token_cipher)
    await delivery_manager.start_saved_bots()

    dp = Dispatcher(storage=MemoryStorage())
    dp["storage"] = storage
    dp["token_cipher"] = token_cipher
    dp["delivery_manager"] = delivery_manager
    dp.update.outer_middleware(DatabaseMiddleware(factory))
    dp.update.outer_middleware(WorkspaceMiddleware())
    dp.include_router(admin_router)
    dp.errors.register(on_error)
    log.info("Bot starting")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        log.info("Bot stopping")
        await delivery_manager.shutdown()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
