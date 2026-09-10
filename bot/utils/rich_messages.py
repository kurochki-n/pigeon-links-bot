from collections.abc import Callable
from functools import wraps
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

ButtonRows = list[list[InlineKeyboardButton]]


def _button_text(text: str) -> str:
    clean = text.strip()
    if not clean:
        raise ValueError("Button text cannot be empty")
    return clean if len(clean) <= 64 else f"{clean[:63]}…"


def callback_button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=_button_text(text), callback_data=callback_data)


def url_button(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=_button_text(text), url=url)


def inline_keyboard(rows: ButtonRows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def as_markup(
    factory: Callable[..., ButtonRows],
) -> Callable[..., InlineKeyboardMarkup]:
    """Adapt a keyboard row factory to aiogram's native markup type."""

    @wraps(factory)
    def wrapped(*args: Any, **kwargs: Any) -> InlineKeyboardMarkup:
        return inline_keyboard(factory(*args, **kwargs))

    return wrapped
