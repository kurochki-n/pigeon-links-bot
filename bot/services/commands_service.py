import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Мой кабинет"),
    BotCommand(command="channel", description="Настройка канала"),
    BotCommand(command="add", description="Создать умную ссылку"),
    BotCommand(command="links", description="Умные ссылки"),
    BotCommand(command="post", description="Создать пост"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="cancel", description="Отменить действие"),
]


async def setup_bot_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
    except TelegramAPIError as exc:
        log.warning("Could not set private-chat command menu: %s", exc)


async def remove_admin_commands(bot: Bot, telegram_id: int) -> None:
    """Compatibility no-op: workspaces no longer have administrators."""
