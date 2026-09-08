from dataclasses import dataclass
from html import escape
from typing import Protocol

from aiogram.types import InputRichMessage, Message


@dataclass(frozen=True)
class RichButton:
    text: str
    callback_data: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("A rich button needs exactly one action")


RichButtons = list[list[RichButton]]


def is_rich_buttons(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(button, RichButton)
        for row in value
        if isinstance(row, list)
        for button in row
    )


def callback_button(text: str, callback_data: str) -> RichButton:
    return RichButton(text=text, callback_data=callback_data)


def url_button(text: str, url: str) -> RichButton:
    return RichButton(text=text, url=url)


def rich_message(text: str, buttons: RichButtons) -> InputRichMessage:
    """Build a Bot API Rich Message with interactive buttons embedded in its HTML."""
    rows = []
    for row in buttons:
        rendered = []
        for button in row:
            if button.callback_data is not None:
                action = f'type="callback_data" data="{escape(button.callback_data, quote=True)}"'
            else:
                action = f'type="url" url="{escape(button.url or "", quote=True)}"'
            rendered.append(f"<tg-button {action}>{escape(button.text)}</tg-button>")
        rows.append(f"<tg-button-row>{''.join(rendered)}</tg-button-row>")
    body = text or "<p></p>"
    return InputRichMessage(html=f"{body}{''.join(rows)}")


class RichMessageTarget(Protocol):
    async def answer_rich(
        self, rich_message: InputRichMessage, **kwargs: object
    ) -> Message: ...


async def answer_rich(
    target: RichMessageTarget, text: str, buttons: RichButtons
) -> Message:
    return await target.answer_rich(rich_message(text, buttons))


_message_answer = Message.answer


async def _answer_with_rich_buttons(
    self: Message, text: str, **kwargs: object
) -> Message:
    buttons = kwargs.pop("reply_markup", None)
    if is_rich_buttons(buttons):
        return await self.answer_rich(rich_message(text, buttons))
    if buttons is not None:
        kwargs["reply_markup"] = buttons
    return await _message_answer(self, text, **kwargs)


Message.answer = _answer_with_rich_buttons
