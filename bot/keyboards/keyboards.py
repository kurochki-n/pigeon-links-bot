from aiogram.types import (
    ChatAdministratorRights,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
)

from bot.keyboards.callbacks import (
    ChannelCallback,
    LinkCallback,
    MaterialCallback,
    PostCallback,
    SourceCallback,
    StatsCallback,
    SubscriptionCallback,
)
from bot.utils.rich_messages import ButtonRows, as_markup, callback_button, url_button

CHANNEL_REQUEST_ID = 1001

_CHANNEL_RIGHTS = ChatAdministratorRights(
    is_anonymous=False,
    can_manage_chat=True,
    can_delete_messages=False,
    can_manage_video_chats=False,
    can_restrict_members=False,
    can_promote_members=False,
    can_change_info=False,
    can_invite_users=False,
    can_post_stories=False,
    can_edit_stories=False,
    can_delete_stories=False,
    can_send_welcome_messages=False,
    can_post_messages=True,
)


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
                        user_administrator_rights=_CHANNEL_RIGHTS,
                        bot_administrator_rights=_CHANNEL_RIGHTS,
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


@as_markup
def channel_menu(configured: bool) -> ButtonRows:
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


@as_markup
def confirm(action: str, link_id: int = 0) -> ButtonRows:
    return [
        [
            callback_button(
                "Да, отключить", LinkCallback(action=action, link_id=link_id).pack()
            ),
            callback_button("Отмена", LinkCallback(action="cancel").pack()),
        ]
    ]


@as_markup
def material_type_menu() -> ButtonRows:
    return [
        [
            callback_button(
                "Текст, файл, фото или видео",
                MaterialCallback(action="telegram").pack(),
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


@as_markup
def fallback_files_menu() -> ButtonRows:
    return [
        [
            callback_button(
                "Готово — продолжить", MaterialCallback(action="fallback_done").pack()
            )
        ],
        [callback_button("Отмена", MaterialCallback(action="fallback_cancel").pack())],
    ]


@as_markup
def github_download_keyboard(link_id: int) -> ButtonRows:
    return [
        [
            callback_button(
                "Скачать ZIP-архив",
                LinkCallback(action="download_github", link_id=link_id).pack(),
            )
        ]
    ]


@as_markup
def github_preview_keyboard() -> ButtonRows:
    return [
        [
            callback_button(
                "Скачать ZIP-архив", LinkCallback(action="preview_github").pack()
            )
        ]
    ]


@as_markup
def link_preview() -> ButtonRows:
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


@as_markup
def link_actions(link_id: int, url: str) -> ButtonRows:
    return [
        [url_button("Перейти по ссылке", url)],
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


@as_markup
def links_page(items: list[object], page: int, total: int) -> ButtonRows:
    rows = [
        [callback_button(x.name, LinkCallback(action="show", link_id=x.id).pack())]
        for x in items
    ]
    nav = []
    if page:
        nav.append(
            callback_button("Назад", LinkCallback(action="page", page=page - 1).pack())
        )
    if (page + 1) * 8 < total:
        nav.append(
            callback_button("Вперёд", LinkCallback(action="page", page=page + 1).pack())
        )
    if nav:
        rows.append(nav)
    return rows


@as_markup
def source_actions(source_id: int, link_id: int, url: str) -> ButtonRows:
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
                "Посмотреть статистику",
                SourceCallback(action="stats", source_id=source_id).pack(),
            ),
            callback_button(
                "Изменить название",
                SourceCallback(action="rename", source_id=source_id).pack(),
            ),
            callback_button(
                "Отключить источник",
                SourceCallback(action="disable", source_id=source_id).pack(),
            ),
        ],
    ]


@as_markup
def subscribe_keyboard(url: str, link_id: int, source_id: int = 0) -> ButtonRows:
    return [
        [url_button("1. Подписаться на канал", url)],
        [
            callback_button(
                "2. Я подписался — получить материал",
                SubscriptionCallback(link_id=link_id, source_id=source_id).pack(),
            )
        ],
    ]


@as_markup
def stats_menu() -> ButtonRows:
    return [
        [callback_button("Все ссылки вместе", StatsCallback(action="all").pack())],
        [
            callback_button(
                "Одна выбранная ссылка", StatsCallback(action="links").pack()
            )
        ],
    ]


@as_markup
def report_button(link_id: int = 0) -> ButtonRows:
    return [
        [
            callback_button(
                "Скачать понятный отчёт",
                StatsCallback(action="report", link_id=link_id).pack(),
            )
        ]
    ]


@as_markup
def post_choice() -> ButtonRows:
    return [
        [
            callback_button(
                "Добавить кнопку со ссылкой", PostCallback(action="button").pack()
            )
        ],
        [callback_button("Не добавлять кнопку", PostCallback(action="preview").pack())],
        [callback_button("Отмена", PostCallback(action="cancel").pack())],
    ]


@as_markup
def post_more() -> ButtonRows:
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


@as_markup
def post_preview() -> ButtonRows:
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
