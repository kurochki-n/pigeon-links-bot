from aiogram.types import (
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
)

from bot.keyboards.callbacks import (
    AdminCallback,
    ChannelCallback,
    LinkCallback,
    MaterialCallback,
    PostCallback,
    SourceCallback,
    StatsCallback,
    SubscriptionCallback,
)
from bot.utils.rich_messages import RichButtons, callback_button, url_button

CHANNEL_REQUEST_ID = 1001


def admin_menu() -> RichButtons:
    return [
        [callback_button("Пригласить помощника", AdminCallback(action="add").pack())],
        [callback_button("Убрать помощника", AdminCallback(action="remove").pack())],
    ]


def channel_selector() -> ReplyKeyboardMarkup:
    """System chat picker; works even if channel content cannot be forwarded."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="Выбрать канал",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=CHANNEL_REQUEST_ID,
                        chat_is_channel=True,
                        bot_is_member=True,
                        request_title=True,
                        request_username=True,
                    ),
                )
            ],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def channel_menu(configured: bool) -> RichButtons:
    rows = [
        [
            callback_button(
                "Выбрать другой канал" if configured else "Подключить канал",
                ChannelCallback(action="change" if configured else "add").pack(),
            )
        ]
    ]
    if configured:
        rows.append(
            [
                callback_button(
                    "Отключить этот канал", ChannelCallback(action="delete").pack()
                )
            ]
        )
    return rows


def confirm(action: str, link_id: int = 0) -> RichButtons:
    return [
        [
            callback_button(
                "Да, удалить", LinkCallback(action=action, link_id=link_id).pack()
            ),
            callback_button("Отмена", LinkCallback(action="cancel").pack()),
        ]
    ]


def material_type_menu() -> RichButtons:
    return [
        [
            callback_button(
                "Файл, фото или видео", MaterialCallback(action="telegram").pack()
            )
        ],
        [callback_button("Архив из GitHub", MaterialCallback(action="github").pack())],
        [
            callback_button(
                "Только моё сообщение", MaterialCallback(action="none").pack()
            )
        ],
        [callback_button("Отмена", MaterialCallback(action="cancel").pack())],
    ]


def fallback_files_menu() -> RichButtons:
    return [
        [
            callback_button(
                "Готово — продолжить", MaterialCallback(action="fallback_done").pack()
            )
        ],
        [callback_button("Отмена", MaterialCallback(action="fallback_cancel").pack())],
    ]


def github_download_keyboard(link_id: int) -> RichButtons:
    return [
        [
            callback_button(
                "Скачать архив ZIP",
                LinkCallback(action="download_github", link_id=link_id).pack(),
            )
        ]
    ]


def github_preview_keyboard() -> RichButtons:
    return [
        [
            callback_button(
                "Скачать архив ZIP", LinkCallback(action="preview_github").pack()
            )
        ]
    ]


def link_preview() -> RichButtons:
    return [
        [
            callback_button(
                "Сохранить и получить ссылку", LinkCallback(action="save").pack()
            )
        ],
        [
            callback_button("Начать заново", LinkCallback(action="restart").pack()),
            callback_button("Отмена", LinkCallback(action="cancel").pack()),
        ],
    ]


def link_actions(link_id: int, url: str) -> RichButtons:
    return [
        [url_button("Открыть ссылку для людей", url)],
        [
            callback_button(
                "Источники переходов",
                SourceCallback(action="list", link_id=link_id).pack(),
            )
        ],
        [
            callback_button(
                "Посмотреть статистику",
                StatsCallback(action="one", link_id=link_id).pack(),
            ),
            callback_button(
                "Отключить ссылку",
                LinkCallback(action="ask_delete", link_id=link_id).pack(),
            ),
        ],
    ]


def links_page(items: list[object], page: int) -> RichButtons:
    rows = [
        [callback_button(x.name, LinkCallback(action="show", link_id=x.id).pack())]
        for x in items
    ]
    nav = []
    if page:
        nav.append(
            callback_button("Назад", LinkCallback(action="page", page=page - 1).pack())
        )
    if len(items) == 8:
        nav.append(
            callback_button("Вперёд", LinkCallback(action="page", page=page + 1).pack())
        )
    if nav:
        rows.append(nav)
    return rows


def source_actions(source_id: int, link_id: int, url: str) -> RichButtons:
    return [
        [url_button("Открыть ссылку источника", url)],
        [
            callback_button(
                "Добавить источник",
                SourceCallback(action="add", link_id=link_id).pack(),
            )
        ],
        [
            callback_button(
                "Статистика", SourceCallback(action="stats", source_id=source_id).pack()
            ),
            callback_button(
                "Переименовать",
                SourceCallback(action="rename", source_id=source_id).pack(),
            ),
            callback_button(
                "Отключить",
                SourceCallback(action="disable", source_id=source_id).pack(),
            ),
        ],
    ]


def subscribe_keyboard(url: str, link_id: int, source_id: int = 0) -> RichButtons:
    return [
        [url_button("1. Подписаться на канал", url)],
        [
            callback_button(
                "2. Я подписался — получить материал",
                SubscriptionCallback(link_id=link_id, source_id=source_id).pack(),
            )
        ],
    ]


def stats_menu() -> RichButtons:
    return [
        [callback_button("Все ссылки вместе", StatsCallback(action="all").pack())],
        [
            callback_button(
                "Одна выбранная ссылка", StatsCallback(action="links").pack()
            )
        ],
    ]


def report_button(link_id: int = 0) -> RichButtons:
    return [
        [
            callback_button(
                "Скачать понятный отчёт",
                StatsCallback(action="report", link_id=link_id).pack(),
            )
        ]
    ]


def post_choice() -> RichButtons:
    return [
        [
            callback_button(
                "Добавить кнопку со ссылкой", PostCallback(action="button").pack()
            )
        ],
        [callback_button("Не добавлять кнопку", PostCallback(action="preview").pack())],
        [callback_button("Отмена", PostCallback(action="cancel").pack())],
    ]


def post_more() -> RichButtons:
    return [
        [
            callback_button(
                "Добавить ещё одну кнопку", PostCallback(action="button").pack()
            )
        ],
        [
            callback_button(
                "Посмотреть перед публикацией", PostCallback(action="preview").pack()
            )
        ],
    ]


def post_preview() -> RichButtons:
    return [
        [
            callback_button(
                "Да, опубликовать в канал", PostCallback(action="publish").pack()
            )
        ],
        [
            callback_button("Начать заново", PostCallback(action="restart").pack()),
            callback_button("Отмена", PostCallback(action="cancel").pack()),
        ],
    ]
