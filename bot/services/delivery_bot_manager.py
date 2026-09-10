import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.repositories.repositories import DeliveryBotRepository
from bot.handlers.user.start import create_delivery_router
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.workspace import WorkspaceMiddleware
from bot.services.commands_service import setup_delivery_bot_commands
from bot.services.storage_service import FileStorageService
from bot.services.token_cipher import TokenCipher

log = logging.getLogger(__name__)


async def on_delivery_error(event: ErrorEvent, bot: Bot) -> bool:
    exception = event.exception
    log.error(
        "Unhandled delivery bot update",
        exc_info=(type(exception), exception, exception.__traceback__),
    )
    update = event.update
    user = getattr(getattr(update, "message", None), "from_user", None) or getattr(
        getattr(update, "callback_query", None), "from_user", None
    )
    if user:
        try:
            await bot.send_message(
                user.id, "Произошла временная ошибка. Попробуйте ещё раз позже."
            )
        except TelegramAPIError:
            pass
    return True


class DeliveryBotManager:
    """Starts and stops one polling task for every configured delivery bot."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        storage: FileStorageService,
        cipher: TokenCipher,
    ) -> None:
        self.factory = factory
        self.storage = storage
        self.cipher = cipher
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._bots: dict[int, Bot] = {}
        self._lock = asyncio.Lock()

    async def start_saved_bots(self) -> None:
        async with self.factory() as session:
            items = await DeliveryBotRepository(session).all()
        for item in items:
            try:
                token = self.cipher.decrypt(item.encrypted_token)
            except ValueError:
                log.exception(
                    "Delivery bot token cannot be decrypted: owner_id=%s", item.owner_id
                )
                continue
            await self.replace(item.owner_id, token)

    async def replace(self, owner_id: int, token: str) -> None:
        async with self._lock:
            await self._stop_unlocked(owner_id)
            task = asyncio.create_task(
                self._run(owner_id, token), name=f"delivery-bot-{owner_id}"
            )
            self._tasks[owner_id] = task
            task.add_done_callback(
                lambda finished, owner=owner_id: self._task_finished(owner, finished)
            )

    def get_bot(self, owner_id: int) -> Bot | None:
        return self._bots.get(owner_id)

    async def stop(self, owner_id: int) -> None:
        async with self._lock:
            await self._stop_unlocked(owner_id)

    async def shutdown(self) -> None:
        async with self._lock:
            owners = list(self._tasks)
            for owner_id in owners:
                await self._stop_unlocked(owner_id)

    async def _stop_unlocked(self, owner_id: int) -> None:
        task = self._tasks.pop(owner_id, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _task_finished(self, owner_id: int, task: asyncio.Task[None]) -> None:
        if self._tasks.get(owner_id) is task:
            self._tasks.pop(owner_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error:
            log.error(
                "Delivery bot stopped unexpectedly: owner_id=%s",
                owner_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _run(self, owner_id: int, token: str) -> None:
        bot = Bot(
            token,
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML,
                link_preview_is_disabled=True,
            ),
        )
        self._bots[owner_id] = bot
        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher["storage"] = self.storage
        dispatcher["delivery_owner_id"] = owner_id
        dispatcher.update.outer_middleware(DatabaseMiddleware(self.factory))
        dispatcher.update.outer_middleware(WorkspaceMiddleware())
        dispatcher.include_router(create_delivery_router())
        dispatcher.errors.register(on_delivery_error)
        await setup_delivery_bot_commands(bot)
        me = await bot.get_me()
        if (me.username or "").casefold() != "pigeonlinksbot":
            try:
                await bot.set_my_short_description(
                    short_description="Оригинальный бот: @PigeonLinksBot"
                )
            except TelegramAPIError as exc:
                log.warning("Could not update delivery bot short description: %s", exc)
        log.info(
            "Delivery bot started: owner_id=%s username=@%s", owner_id, me.username
        )
        try:
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                handle_signals=False,
            )
        finally:
            if self._bots.get(owner_id) is bot:
                self._bots.pop(owner_id, None)
            await bot.session.close()
