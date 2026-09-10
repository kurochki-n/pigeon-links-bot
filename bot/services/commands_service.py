import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Показать, с чего начать"),
    BotCommand(command="bot", description="Подключить бота для подписчиков"),
    BotCommand(command="channel", description="Подключить мой канал"),
    BotCommand(command="add", description="Создать ссылку для материала"),
    BotCommand(command="links", description="Мои ссылки и источники"),
    BotCommand(command="post", description="Опубликовать пост в канале"),
    BotCommand(command="stats", description="Посмотреть результаты"),
    BotCommand(command="cancel", description="Отменить текущий шаг"),
]


DELIVERY_COMMANDS = [
    BotCommand(command="start", description="Получить материал по ссылке"),
]


async def _set_commands(bot: Bot, commands: list[BotCommand]) -> None:
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
    except TelegramAPIError as exc:
        log.warning("Could not set bot command menu: %s", exc)


async def setup_bot_commands(bot: Bot) -> None:
    await _set_commands(bot, COMMANDS)


async def setup_delivery_bot_commands(bot: Bot) -> None:
    await _set_commands(bot, DELIVERY_COMMANDS)
