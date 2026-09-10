import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Показать, с чего начать"),
    BotCommand(command="channel", description="Подключить мой канал"),
    BotCommand(command="add", description="Создать ссылку для материала"),
    BotCommand(command="links", description="Мои ссылки и источники"),
    BotCommand(command="post", description="Опубликовать пост в канале"),
    BotCommand(command="stats", description="Посмотреть результаты"),
    BotCommand(command="cancel", description="Отменить текущий шаг"),
]


async def setup_bot_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
    except TelegramAPIError as exc:
        log.warning("Could not set private-chat command menu: %s", exc)


async def remove_admin_commands(bot: Bot, telegram_id: int) -> None:
    """Compatibility no-op: workspaces no longer have administrators."""
