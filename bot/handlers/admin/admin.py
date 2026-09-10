import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    Message,
)
from aiogram.utils.token import TokenValidationError
from pydantic import HttpUrl, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.repositories import (
    ChannelConnectTokenRepository,
    ChannelRepository,
    DeliveryBotRepository,
    FallbackFileRepository,
    LinkRepository,
    SourceRepository,
    VisitRepository,
)
from bot.keyboards.callbacks import (
    ChannelCallback,
    LinkCallback,
    MaterialCallback,
    PostCallback,
    SourceCallback,
    StatsCallback,
)
from bot.keyboards.keyboards import (
    channel_menu,
    confirm,
    fallback_files_menu,
    link_actions,
    link_preview,
    links_page,
    material_type_menu,
    post_choice,
    post_more,
    post_preview,
    report_button,
    source_actions,
    stats_menu,
)
from bot.services.delivery_bot_manager import DeliveryBotManager
from bot.services.github_service import GithubService
from bot.services.services import (
    LinkService,
    TrafficSourceService,
    send_link_content,
    stats_text,
)
from bot.services.storage_service import FileStorageService
from bot.services.token_cipher import TokenCipher
from bot.states.flows import DeliveryBotFlow, PostFlow, SmartLinkFlow, SourceFlow
from bot.utils.html_report import create_report
from bot.utils.rich_messages import callback_button, inline_keyboard, url_button
from config import settings

router = Router(name="admin")
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

MAX_MESSAGE_TEXT_LENGTH = 3500
MAX_POST_BUTTONS = 100
MAX_FALLBACK_FILES = 8
MAX_SOURCES_PER_LINK = 100


def content(message: Message) -> dict[str, object] | None:
    if message.text:
        return {
            "content_type": "text",
            "text": message.html_text,
            "caption": None,
            "file_id": None,
        }
    for field, kind in [
        ("photo", "photo"),
        ("video", "video"),
        ("document", "document"),
        ("animation", "animation"),
    ]:
        value = getattr(message, field, None)
        if value:
            if field == "photo":
                value = value[-1]
            original_filename = getattr(value, "file_name", None) or f"material.{kind}"
            return {
                "content_type": kind,
                "text": None,
                "caption": message.html_text,
                "file_id": value.file_id,
                "original_filename": original_filename,
                "mime_type": getattr(value, "mime_type", None),
                "file_size": getattr(value, "file_size", None),
            }
    return None


def fallback_file(message: Message, position: int) -> dict[str, object] | None:
    for field, kind in [
        ("photo", "photo"),
        ("video", "video"),
        ("document", "document"),
        ("animation", "animation"),
    ]:
        value = getattr(message, field, None)
        if value:
            if field == "photo":
                value = value[-1]
            name = (
                getattr(value, "file_name", None) or f"fallback_{position + 1}.{kind}"
            )
            return {
                "file_id": value.file_id,
                "file_type": kind,
                "file_name": name,
                "original_filename": name,
                "mime_type": getattr(value, "mime_type", None),
                "file_size": getattr(value, "file_size", None),
            }
    return None


def file_is_too_large(file: dict[str, object]) -> bool:
    size = file.get("file_size")
    return isinstance(size, int) and size > settings.max_telegram_file_size


async def cleanup_link_draft(state: FSMContext, storage: FileStorageService) -> None:
    data = await state.get_data()
    content = data.get("content", {})
    await storage.delete(content.get("relative_path"))
    for item in data.get("content_items", []):
        await storage.delete(item.get("relative_path"))
    for file in data.get("fallback_files", []):
        await storage.delete(file.get("relative_path"))


async def cleanup_post_draft(state: FSMContext, storage: FileStorageService) -> None:
    data = await state.get_data()
    for item in data.get("content_items", []):
        await storage.delete(item.get("relative_path"))
    await storage.delete(data.get("content", {}).get("relative_path"))


async def delivery_url(session: AsyncSession, payload: str) -> str:
    delivery_bot = await DeliveryBotRepository(session).get()
    if delivery_bot is None:
        raise RuntimeError("Delivery bot is not configured")
    return f"https://t.me/{delivery_bot.username}?start={payload}"


@router.message(Command("cancel"))
async def cancel(
    message: Message, state: FSMContext, storage: FileStorageService
) -> None:
    current_state = await state.get_state()
    if current_state:
        smart_link_states = {
            SmartLinkFlow.name.state,
            SmartLinkFlow.message.state,
            SmartLinkFlow.material_type.state,
            SmartLinkFlow.content.state,
            SmartLinkFlow.resources.state,
            SmartLinkFlow.github_url.state,
            SmartLinkFlow.github_fallbacks.state,
            SmartLinkFlow.preview.state,
        }
        if current_state in smart_link_states:
            await cleanup_link_draft(state, storage)
        post_states = {
            PostFlow.content.state,
            PostFlow.button_choice.state,
            PostFlow.button_text.state,
            PostFlow.button_url.state,
            PostFlow.button_more.state,
            PostFlow.preview.state,
        }
        if current_state in post_states:
            await cleanup_post_draft(state, storage)
        await state.clear()
        await message.answer("Действие отменено.")
    else:
        await message.answer(
            "Сейчас нечего отменять. Начните с /bot, чтобы подключить бота для подписчиков."
        )


@router.message(Command("start"))
async def dashboard(message: Message) -> None:
    await message.answer(
        "<b>Это бот настроек.</b>\n\n"
        "Сначала подключите отдельного бота для подписчиков: /bot\n"
        "Затем подключите канал: /channel\n"
        "После этого создайте ссылку с материалом: /add\n\n"
        "/links — ваши ссылки\n/stats — результаты\n/post — публикация в канал\n"
        "/cancel — отменить текущий шаг"
    )


@router.message(Command("bot"))
async def delivery_bot_setup(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    current = await DeliveryBotRepository(session).get()
    current_text = (
        f"\n\nСейчас подключён: @{escape(current.username)}. Новый токен заменит этого бота. Старые опубликованные ссылки нужно будет заменить."
        if current
        else ""
    )
    await state.set_state(DeliveryBotFlow.token)
    await message.answer(
        "<b>Подключение бота для подписчиков</b>\n\n"
        "1. Откройте @BotFather.\n"
        "2. Отправьте /newbot и создайте нового бота.\n"
        "3. Скопируйте токен, который пришлёт BotFather.\n"
        "4. Пришлите этот токен сюда одним сообщением.\n\n"
        "Токен выглядит примерно так: <code>123456789:AA...</code>. "
        "Бот удалит ваше сообщение с токеном и сохранит его в зашифрованном виде."
        f"{current_text}"
    )


@router.message(DeliveryBotFlow.token)
async def save_delivery_bot(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    token_cipher: TokenCipher,
    delivery_manager: DeliveryBotManager,
    commit_cleanups: list,
) -> None:
    token = (message.text or "").strip()
    token_message_deleted = True
    try:
        await message.delete()
    except TelegramAPIError:
        token_message_deleted = False
    delete_warning = (
        "\n\n⚠️ Telegram не разрешил удалить сообщение с токеном. Удалите его вручную из чата."
        if not token_message_deleted
        else ""
    )
    candidate: Bot | None = None
    try:
        candidate = Bot(token)
        info = await candidate.get_me()
    except (TelegramAPIError, TokenValidationError, ValueError):
        await message.answer(
            "Токен не подошёл. Скопируйте его заново из @BotFather и пришлите без пробелов и кавычек."
            f"{delete_warning}"
        )
        return
    finally:
        if candidate is not None:
            await candidate.session.close()
    if info.id == (await bot.get_me()).id:
        await message.answer(
            "Нельзя подключить бот настроек к самому себе. Создайте отдельного бота через @BotFather."
            f"{delete_warning}"
        )
        return
    repository = DeliveryBotRepository(session)
    current = await repository.get()
    used = await repository.by_bot_id(info.id)
    if used and used.owner_id != message.from_user.id:
        await message.answer(
            "Этот бот уже подключён к другому кабинету. Создайте другого бота через @BotFather."
            f"{delete_warning}"
        )
        return
    bot_changed = current is not None and current.bot_id != info.id
    channel_must_reconnect = current is None or bot_changed
    await repository.save(
        info.id, info.username or str(info.id), token_cipher.encrypt(token)
    )
    if channel_must_reconnect:
        await ChannelRepository(session).delete()
    await state.clear()
    commit_cleanups.append(
        lambda: delivery_manager.replace(message.from_user.id, token)
    )
    warning = (
        "\n\nВы заменили бота. Старый канал отключён, а опубликованные ранее ссылки ведут к старому боту. Подключите канал заново и замените ссылки в публикациях."
        if bot_changed
        else ""
    )
    await message.answer(
        f"✅ Бот @{escape(info.username or str(info.id))} подключён и запускается."
        f"{warning}{delete_warning}\n\nТеперь отправьте /channel, чтобы подключить канал."
    )


async def send_channel_connection_link(
    target: Message,
    session: AsyncSession,
    delivery_manager: DeliveryBotManager,
    owner_id: int,
) -> None:
    delivery_bot = await DeliveryBotRepository(session).get()
    if delivery_bot is None:
        await target.answer(
            "Сначала подключите отдельного бота для подписчиков с помощью команды /bot."
        )
        return
    if delivery_manager.get_bot(owner_id) is None:
        await target.answer(
            "Бот для подписчиков сейчас не запущен. Подключите его заново с помощью команды /bot."
        )
        return
    token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    await ChannelConnectTokenRepository(session).create(
        token_hash,
        owner_id,
        datetime.now(UTC) + timedelta(minutes=15),
    )
    connect_url = f"https://t.me/{delivery_bot.username}?start=connect_{token}"
    await target.answer(
        f"<b>Подключение канала к @{escape(delivery_bot.username)}</b>\n\n"
        "Нажмите кнопку ниже. Откроется бот для подписчиков и покажет следующие шаги. "
        "Ссылка действует 15 минут и подходит только вашему аккаунту.",
        reply_markup=inline_keyboard(
            [[url_button("Продолжить подключение канала", connect_url)]]
        ),
    )


@router.message(Command("channel"))
async def channel(
    message: Message,
    session: AsyncSession,
    delivery_manager: DeliveryBotManager,
) -> None:
    if await DeliveryBotRepository(session).get() is None:
        await message.answer(
            "Сначала подключите отдельного бота для подписчиков с помощью команды /bot."
        )
        return
    if delivery_manager.get_bot(message.from_user.id) is None:
        await message.answer(
            "Бот для подписчиков сейчас не запущен. Подключите его заново через /bot."
        )
        return
    item = await ChannelRepository(session).get()
    if item is None:
        await send_channel_connection_link(
            message, session, delivery_manager, message.from_user.id
        )
        return
    await message.answer(
        f"<b>Подключённый канал</b>\n\nНазвание: {escape(item.title)}\n"
        f"Имя в Telegram: {escape('@' + item.username if item.username else 'не задано')}\n"
        f"Внутренний номер: <code>{item.channel_id}</code>",
        reply_markup=channel_menu(True),
    )


@router.callback_query(ChannelCallback.filter(F.action.in_({"add", "change"})))
async def channel_input(
    callback: CallbackQuery,
    session: AsyncSession,
    delivery_manager: DeliveryBotManager,
) -> None:
    await callback.answer()
    await send_channel_connection_link(
        callback.message, session, delivery_manager, callback.from_user.id
    )


@router.callback_query(ChannelCallback.filter(F.action == "delete"))
async def channel_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    await ChannelRepository(session).delete()
    await callback.message.answer(
        "Канал отключён. Пока не подключите новый канал через /channel, ссылки не смогут проверять подписку."
    )


@router.message(Command("add"))
async def add_link(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    delivery_manager: DeliveryBotManager,
) -> None:
    if not await DeliveryBotRepository(session).get():
        await message.answer(
            "Сначала подключите отдельного бота для подписчиков с помощью команды /bot."
        )
        return
    if delivery_manager.get_bot(message.from_user.id) is None:
        await message.answer(
            "Бот для подписчиков сейчас не запущен. Подключите его заново через /bot."
        )
        return
    if not await ChannelRepository(session).get():
        await message.answer(
            "Сначала подключите канал, подписку на который будет проверять бот. Отправьте /channel и выполните три шага на экране."
        )
        return
    await state.set_state(SmartLinkFlow.name)
    await message.answer(
        "<b>Создадим ссылку для выдачи материала.</b>\n\n"
        "Напишите её понятное название для себя. Его увидите только вы.\n"
        "Например: «Реклама у блогера» или «Подарок с сайта»."
    )


@router.message(SmartLinkFlow.name)
async def link_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 128:
        await message.answer(
            "Напишите непустое название длиной до 128 символов. Например: «Подарок с сайта»."
        )
        return
    await state.update_data(name=name, content_items=[], resources=[])
    await state.set_state(SmartLinkFlow.content)
    await message.answer(
        "Теперь пришлите готовое сообщение для подписчика. Можно отправить текст, фото, видео, GIF и документы — несколькими сообщениями в нужном порядке. У фото или видео можно добавить подпись.\n\n"
        "Когда добавите всё, нажмите «Готово — перейти к ссылкам».",
        reply_markup=inline_keyboard(
            [
                [
                    callback_button(
                        "Готово — перейти к ссылкам",
                        MaterialCallback(action="content_done").pack(),
                    )
                ]
            ]
        ),
    )


@router.message(F.text == "__legacy_link_message__")
async def link_message(message: Message, state: FSMContext) -> None:
    if not (message.text or "").strip():
        await message.answer(
            "Пришлите обычное текстовое сообщение, которое увидит человек."
        )
        return
    if len(message.text) > MAX_MESSAGE_TEXT_LENGTH:
        await message.answer(
            f"Сообщение слишком длинное. Сократите его до {MAX_MESSAGE_TEXT_LENGTH} символов."
        )
        return
    await state.update_data(message_text=message.html_text)
    await state.set_state(SmartLinkFlow.material_type)
    await message.answer(
        "Что человек должен получить после подписки? Выберите вариант ниже.",
        reply_markup=material_type_menu(),
    )


@router.callback_query(
    MaterialCallback.filter(F.action == "telegram"), SmartLinkFlow.material_type
)
async def choose_telegram_material(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(SmartLinkFlow.content)
    await callback.message.answer(
        "Пришлите сюда один материал: текст, фото, видео, документ или GIF. Бот сохранит его и будет отправлять подписчикам."
    )


@router.callback_query(
    MaterialCallback.filter(F.action == "github"), SmartLinkFlow.material_type
)
async def choose_github_material(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(SmartLinkFlow.github_url)
    await callback.message.answer(
        "Пришлите ссылку на публичный проект GitHub. Она выглядит так:\n"
        "https://github.com/владелец/название-проекта\n\n"
        "После подписки человек сможет скачать свежий ZIP-архив этого проекта."
    )


@router.callback_query(
    MaterialCallback.filter(F.action == "none"), SmartLinkFlow.material_type
)
async def choose_no_material(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    await callback.answer()
    await state.update_data(
        content={
            "content_type": "none",
            "text": None,
            "caption": None,
            "file_id": None,
            "github_owner": None,
            "github_repo": None,
            "github_url": None,
        }
    )
    await show_link_preview(callback.message, state, bot)


@router.callback_query(
    MaterialCallback.filter(F.action == "cancel"), SmartLinkFlow.material_type
)
async def cancel_material_selection(
    callback: CallbackQuery, state: FSMContext, storage: FileStorageService
) -> None:
    await callback.answer()
    await cleanup_link_draft(state, storage)
    await state.clear()
    await callback.message.answer("Действие отменено.")


@router.message(SmartLinkFlow.content)
async def link_content(
    message: Message,
    state: FSMContext,
    bot: Bot,
    storage: FileStorageService,
    rollback_cleanups: list,
) -> None:
    item = content(message)
    if not item:
        await message.answer(
            "Я не могу использовать сообщение такого типа. Пришлите текст, фото, видео, документ или GIF."
        )
        return
    if item["content_type"] != "text":
        if file_is_too_large(item):
            limit_mb = settings.max_telegram_file_size // (1024 * 1024)
            await message.answer(
                f"Этот файл слишком большой. Пришлите файл размером не больше {limit_mb} МБ."
            )
            return
        try:
            stored = await storage.save_telegram_file(
                bot,
                item["file_id"] or "",
                "smart_links/pending",
                item["original_filename"] or "material",
                item.get("mime_type"),
            )
        except (TelegramAPIError, OSError, ValueError):
            await message.answer(
                "Не удалось сохранить файл. Попробуйте отправить его ещё раз."
            )
            return
        item.update(
            file_id=None,
            relative_path=stored.relative_path,
            original_filename=stored.original_filename,
            mime_type=stored.mime_type,
            file_size=stored.file_size,
        )
        rollback_cleanups.append(lambda: storage.delete(stored.relative_path))
    data = await state.get_data()
    items = data.get("content_items", [])
    items.append(item)
    await state.update_data(content_items=items)
    await message.answer(
        f"Добавлено сообщений: {len(items)}. Пришлите следующее или нажмите «Готово — перейти к ссылкам»."
    )


@router.callback_query(
    MaterialCallback.filter(F.action == "content_done"), SmartLinkFlow.content
)
async def finish_link_content(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if not (await state.get_data()).get("content_items"):
        await callback.message.answer(
            "Сначала пришлите хотя бы одно сообщение или файл."
        )
        return
    await state.set_state(SmartLinkFlow.resources)
    await callback.message.answer(
        "Добавьте ссылки, которые увидит подписчик. Пришлите ссылку на GitHub — по ней будет доступен свежий ZIP-архив. Любая другая ссылка станет обычной кнопкой.\n\n"
        "Можно добавлять сколько угодно ссылок. Когда закончите, нажмите «Готово — посмотреть».",
        reply_markup=inline_keyboard(
            [
                [
                    callback_button(
                        "Готово — посмотреть",
                        MaterialCallback(action="resources_done").pack(),
                    )
                ]
            ]
        ),
    )


@router.message(SmartLinkFlow.resources)
async def add_link_resource(message: Message, state: FSMContext) -> None:
    raw_url = (message.text or "").strip()
    try:
        parsed_url = TypeAdapter(HttpUrl).validate_python(raw_url)
    except ValidationError:
        await message.answer(
            "Пришлите корректную ссылку, начинающуюся с http:// или https://."
        )
        return
    resources = (await state.get_data()).get("resources", [])
    github = await GithubService(settings.max_telegram_file_size).validate_repository(
        raw_url
    )
    if github.status == "ok" and github.repository:
        repository = github.repository
        resource = {
            "resource_type": "github",
            "title": "Скачать ZIP-архив",
            "url": repository.url,
            "github_owner": repository.owner,
            "github_repo": repository.repo,
        }
    else:
        host = parsed_url.host or "ссылку"
        resource = {
            "resource_type": "url",
            "title": f"Открыть {host}",
            "url": str(parsed_url),
            "github_owner": None,
            "github_repo": None,
        }
    resources.append(resource)
    await state.update_data(resources=resources)
    await message.answer(
        f"Ссылка добавлена. Всего ссылок: {len(resources)}. Пришлите следующую или нажмите «Готово — посмотреть»."
    )


@router.callback_query(
    MaterialCallback.filter(F.action == "resources_done"), SmartLinkFlow.resources
)
async def finish_link_resources(
    callback: CallbackQuery, state: FSMContext, bot: Bot, storage: FileStorageService
) -> None:
    await callback.answer()
    await show_link_preview(callback.message, state, bot, storage)


@router.message(SmartLinkFlow.github_url)
async def github_url_input(message: Message, state: FSMContext, bot: Bot) -> None:
    validation = await GithubService(
        settings.max_telegram_file_size
    ).validate_repository(message.text or "")
    if validation.status == "invalid_url":
        await message.answer(
            "Пришлите ссылку на главную страницу проекта, например: https://github.com/owner/project"
        )
        return
    if validation.status == "not_found":
        await message.answer(
            "Проект GitHub не найден. Проверьте, что ссылка верная и проект открыт для всех."
        )
        return
    if validation.status == "private":
        await message.answer(
            "Закрытые проекты GitHub не поддерживаются. Сделайте проект публичным или выберите обычный файл."
        )
        return
    if validation.status != "ok" or not validation.repository:
        await message.answer(
            "GitHub сейчас не отвечает. Подождите немного и пришлите ссылку ещё раз."
        )
        return
    repository = validation.repository
    await state.update_data(
        content={
            "content_type": "github_repository",
            "text": None,
            "caption": None,
            "file_id": None,
            "github_owner": repository.owner,
            "github_repo": repository.repo,
            "github_url": repository.url,
        },
        fallback_files=[],
    )
    await state.set_state(SmartLinkFlow.github_fallbacks)
    await message.answer(
        "Теперь пришлите хотя бы один резервный файл.\n\n"
        "Бот отправит его только в том случае, если не сможет скачать свежий архив с GitHub. "
        "Можно добавить несколько файлов. После загрузки нажмите «Готово — продолжить».",
        reply_markup=fallback_files_menu(),
    )


@router.message(SmartLinkFlow.github_fallbacks)
async def add_github_fallback_file(
    message: Message,
    state: FSMContext,
    bot: Bot,
    storage: FileStorageService,
    rollback_cleanups: list,
) -> None:
    data = await state.get_data()
    files = data.get("fallback_files", [])
    if len(files) >= MAX_FALLBACK_FILES:
        await message.answer(
            f"Можно добавить не больше {MAX_FALLBACK_FILES} резервных файлов. Нажмите «Готово — продолжить»."
        )
        return
    file = fallback_file(message, len(files))
    if not file:
        await message.answer(
            "Пришлите фото, видео, документ или GIF. Когда добавите все резервные файлы, нажмите «Готово — продолжить»."
        )
        return
    if file_is_too_large(file):
        limit_mb = settings.max_telegram_file_size // (1024 * 1024)
        await message.answer(
            f"Этот файл слишком большой. Пришлите файл размером не больше {limit_mb} МБ."
        )
        return
    try:
        stored = await storage.save_telegram_file(
            bot,
            file["file_id"] or "",
            "github_fallback/pending",
            file["original_filename"] or "fallback",
            file.get("mime_type"),
        )
    except (TelegramAPIError, OSError, ValueError):
        await message.answer("Не удалось сохранить резервный файл. Попробуйте ещё раз.")
        return
    file.update(
        file_id=None,
        relative_path=stored.relative_path,
        original_filename=stored.original_filename,
        mime_type=stored.mime_type,
        file_size=stored.file_size,
    )
    rollback_cleanups.append(lambda: storage.delete(stored.relative_path))
    files.append(file)
    await state.update_data(fallback_files=files)
    await message.answer(
        f"Резервный файл добавлен. Всего файлов: {len(files)}.",
        reply_markup=fallback_files_menu(),
    )


@router.callback_query(
    MaterialCallback.filter(F.action == "fallback_done"), SmartLinkFlow.github_fallbacks
)
async def finish_github_fallbacks(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    await callback.answer()
    data = await state.get_data()
    if not data.get("fallback_files"):
        await callback.message.answer(
            "Сначала пришлите хотя бы один резервный файл. После этого нажмите «Готово — продолжить»."
        )
        return
    await show_link_preview(callback.message, state, bot)


@router.callback_query(
    MaterialCallback.filter(F.action == "fallback_cancel"),
    SmartLinkFlow.github_fallbacks,
)
async def cancel_github_fallbacks(
    callback: CallbackQuery, state: FSMContext, storage: FileStorageService
) -> None:
    await callback.answer()
    await cleanup_link_draft(state, storage)
    await state.clear()
    await callback.message.answer("Действие отменено.")


async def show_link_preview(
    message: Message,
    state: FSMContext,
    bot: Bot,
    storage: FileStorageService | None = None,
) -> None:
    data = await state.get_data()
    await message.answer("<b>Предпросмотр для подписчика:</b>")
    for item in data.get("content_items", []):
        await send_link_content(
            bot, message.chat.id, type("C", (), item)(), storage=storage
        )
    resources = data.get("resources", [])
    if resources:
        summary = "\n".join(f"• {escape(resource['title'])}" for resource in resources)
        await message.answer("<b>После сообщения будут кнопки:</b>\n" + summary)
    await state.set_state(SmartLinkFlow.preview)
    await message.answer(
        "Проверьте сообщения выше. Если всё верно, нажмите «Сохранить и получить ссылку».\n"
        "До нажатия этой кнопки ссылка ещё не создана.",
        reply_markup=link_preview(),
    )


@router.callback_query(LinkCallback.filter(F.action == "preview_github"))
async def preview_github_download(callback: CallbackQuery) -> None:
    await callback.answer("Это только пример. Сначала сохраните ссылку.")


@router.callback_query(LinkCallback.filter(F.action == "save"), SmartLinkFlow.preview)
async def save_link(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    storage: FileStorageService,
    rollback_cleanups: list,
) -> None:
    await callback.answer()
    data = await state.get_data()
    for item in data.get("content_items", []):
        if item.get("relative_path"):
            rollback_cleanups.append(
                lambda path=item["relative_path"]: storage.delete(path)
            )
    link = await LinkService(session).create(
        data["name"],
        {
            "content_type": "none",
            "message_text": None,
            "text": None,
            "caption": None,
            "file_id": None,
            "relative_path": None,
            "original_filename": None,
            "mime_type": None,
            "file_size": None,
            "github_owner": None,
            "github_repo": None,
            "github_url": None,
        },
        callback.from_user.id,
        content_items=data.get("content_items", []),
        resources=data.get("resources", []),
    )
    target = await delivery_url(session, link.slug)
    await state.clear()
    await callback.message.answer(
        f"✅ Ссылка создана!\n\nНазвание: {escape(link.name)}\n"
        f"Ссылка для людей:\n<code>{target}</code>\n\n"
        "Скопируйте её и разместите в посте, рекламе или сообщении.",
        reply_markup=link_actions(link.id, target),
    )


@router.callback_query(
    LinkCallback.filter(F.action == "restart"), SmartLinkFlow.preview
)
async def restart_link(
    callback: CallbackQuery, state: FSMContext, storage: FileStorageService
) -> None:
    await callback.answer()
    await cleanup_link_draft(state, storage)
    await state.clear()
    await state.set_state(SmartLinkFlow.name)
    await callback.message.answer(
        "Начнём заново. Напишите понятное название ссылки для себя."
    )


@router.message(Command("links"))
async def links(message: Message, session: AsyncSession) -> None:
    if await DeliveryBotRepository(session).get() is None:
        await message.answer(
            "Сначала подключите бота для подписчиков с помощью команды /bot."
        )
        return
    await show_links(message, session, 0)


async def show_links(message: Message, session: AsyncSession, page: int) -> None:
    page = max(page, 0)
    items, total = await LinkRepository(session).page(page)
    text = (
        f"<b>Ваши ссылки</b>\n\nАктивных ссылок: {total}. Выберите нужную:"
        if items
        else "У вас пока нет активных ссылок. Отправьте /add, чтобы создать первую."
    )
    await message.answer(text, reply_markup=links_page(items, page, total))


@router.callback_query(LinkCallback.filter(F.action == "page"))
async def links_paged(
    callback: CallbackQuery, callback_data: LinkCallback, session: AsyncSession
) -> None:
    await callback.answer()
    await show_links(callback.message, session, callback_data.page)


@router.callback_query(LinkCallback.filter(F.action == "show"))
async def link_show(
    callback: CallbackQuery,
    callback_data: LinkCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(callback_data.link_id, True)
    if not link:
        await callback.message.answer("Эта ссылка больше не существует.")
        return
    data = await VisitRepository(session).summary(link.id)
    material = (
        f"\n\nМатериал: архив проекта GitHub\nПроект: {link.github_owner}/{link.github_repo}"
        if link.content_type == "github_repository"
        else ""
    )
    await callback.message.answer(
        stats_text(
            link.name,
            data,
            link.downloads_count if link.content_type == "github_repository" else None,
        )
        + material,
        reply_markup=link_actions(link.id, await delivery_url(session, link.slug)),
    )


@router.callback_query(SourceCallback.filter(F.action == "list"))
async def list_sources(
    callback: CallbackQuery, callback_data: SourceCallback, session: AsyncSession
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(callback_data.link_id)
    if not link:
        await callback.message.answer("Эта ссылка больше не существует.")
        return
    sources = await SourceRepository(session).list_for_link(link.id)
    visits = VisitRepository(session)
    lines = [
        (
            "<b>Отдельные ссылки для разных мест</b>\n"
            "Создайте отдельную ссылку для рекламы, сайта или каждого "
            "партнёра — так вы поймёте, откуда приходят люди."
        )
    ]
    buttons = []
    for source in sources:
        summary = await visits.source_summary(source.id)
        status = "" if source.is_active else " (отключён)"
        lines.append(
            f"\n{escape(source.name)}{status}\nПереходов: {summary['total_visits']}"
        )
        buttons.append(
            [
                callback_button(
                    source.name,
                    SourceCallback(action="show", source_id=source.id).pack(),
                )
            ]
        )
    buttons.append(
        [
            callback_button(
                "Добавить источник",
                SourceCallback(action="add", link_id=link.id).pack(),
            )
        ]
    )
    await callback.message.answer(
        "\n".join(lines), reply_markup=inline_keyboard(buttons)
    )


@router.callback_query(SourceCallback.filter(F.action == "add"))
async def add_source_start(
    callback: CallbackQuery,
    callback_data: SourceCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(callback_data.link_id, active_only=True)
    if not link:
        await callback.message.answer(
            "Эта ссылка уже отключена или принадлежит другому кабинету."
        )
        return
    if await SourceRepository(session).count_for_link(link.id) >= MAX_SOURCES_PER_LINK:
        await callback.message.answer(
            f"Для одной ссылки можно создать не больше {MAX_SOURCES_PER_LINK} источников. Переименуйте или используйте один из уже созданных."
        )
        return
    await state.set_state(SourceFlow.name)
    await state.update_data(source_link_id=callback_data.link_id, source_mode="add")
    await callback.message.answer(
        "Напишите, где вы разместите эту отдельную ссылку. Например: «Telegram Ads», «Блогер Анна» или «Сайт»."
    )


@router.message(SourceFlow.name)
async def add_source_name(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 128:
        await message.answer(
            "Напишите непустое название длиной до 128 символов. Например: «Реклама в Telegram»."
        )
        return
    source_data = await state.get_data()
    if source_data.get("source_mode") == "rename":
        source = await SourceRepository(session).get(source_data.get("source_id"))
        if not source:
            await state.clear()
            await message.answer("Источник больше не существует.")
            return
        source.name = name
        await state.clear()
        await message.answer("Название источника изменено. Ссылка осталась прежней.")
        return
    link_id = source_data.get("source_link_id")
    link = await LinkRepository(session).get(link_id, active_only=True)
    if not link:
        await state.clear()
        await message.answer("Эта ссылка больше не существует.")
        return
    source = await TrafficSourceService(session).create(link.id, name)
    target = await delivery_url(session, source.token)
    await state.clear()
    await message.answer(
        f"✅ Отдельная ссылка создана.\n\nИсточник: {escape(source.name)}\n"
        f"Ссылка:\n<code>{target}</code>\n\n"
        "Используйте её только в этом источнике, чтобы видеть отдельную статистику.",
        reply_markup=source_actions(source.id, link.id, target),
    )


@router.callback_query(SourceCallback.filter(F.action == "show"))
async def show_source(
    callback: CallbackQuery,
    callback_data: SourceCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    source = await SourceRepository(session).get(callback_data.source_id)
    if not source:
        await callback.message.answer("Источник больше не существует.")
        return
    summary = await VisitRepository(session).source_summary(source.id)
    await callback.message.answer(
        stats_text(source.name, summary),
        reply_markup=source_actions(
            source.id, source.smart_link_id, await delivery_url(session, source.token)
        ),
    )


@router.callback_query(SourceCallback.filter(F.action == "stats"))
async def source_stats(
    callback: CallbackQuery, callback_data: SourceCallback, session: AsyncSession
) -> None:
    await callback.answer()
    source = await SourceRepository(session).get(callback_data.source_id)
    if not source:
        await callback.message.answer("Источник больше не существует.")
        return
    await callback.message.answer(
        stats_text(
            source.name, await VisitRepository(session).source_summary(source.id)
        )
    )


@router.callback_query(SourceCallback.filter(F.action == "rename"))
async def rename_source_start(
    callback: CallbackQuery,
    callback_data: SourceCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await callback.answer()
    source = await SourceRepository(session).get(callback_data.source_id)
    if not source:
        await callback.message.answer(
            "Этот источник уже удалён или принадлежит другому кабинету."
        )
        return
    await state.set_state(SourceFlow.name)
    await state.update_data(source_mode="rename", source_id=callback_data.source_id)
    await callback.message.answer(
        "Напишите новое понятное название источника. Сама ссылка при этом не изменится."
    )


@router.callback_query(SourceCallback.filter(F.action == "disable"))
async def disable_source(
    callback: CallbackQuery, callback_data: SourceCallback, session: AsyncSession
) -> None:
    await callback.answer()
    source = await SourceRepository(session).get(callback_data.source_id)
    if not source:
        await callback.message.answer("Источник больше не существует.")
        return
    source.is_active = False
    await callback.message.answer(
        "Источник отключён: его ссылка больше не работает. Собранная статистика сохранена."
    )


@router.callback_query(LinkCallback.filter(F.action == "ask_delete"))
async def link_delete_ask(
    callback: CallbackQuery, callback_data: LinkCallback, session: AsyncSession
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(callback_data.link_id)
    if not link:
        await callback.message.answer("Эта ссылка больше не существует.")
        return
    await callback.message.answer(
        f"Отключить ссылку «{escape(link.name)}»? Люди больше не смогут получить по ней материал, но статистика сохранится.",
        reply_markup=confirm("delete", link.id),
    )


@router.callback_query(LinkCallback.filter(F.action == "delete"))
async def link_delete(
    callback: CallbackQuery,
    callback_data: LinkCallback,
    session: AsyncSession,
    storage: FileStorageService,
    commit_cleanups: list,
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(callback_data.link_id)
    if not link:
        await callback.message.answer("Эта ссылка больше не существует.")
        return
    await LinkRepository(session).deactivate(link)
    stored_paths = [link.relative_path] + [
        file.relative_path
        for file in await FallbackFileRepository(session).list_for_link(link.id)
    ]
    for path in stored_paths:
        if path:
            commit_cleanups.append(lambda path=path: storage.delete(path))
    await callback.message.answer(
        "Ссылка отключена: материал по ней больше не выдаётся. Собранная статистика сохранена."
    )


@router.callback_query(LinkCallback.filter(F.action == "cancel"))
async def link_cancel(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")


@router.message(Command("stats"))
async def stats(message: Message) -> None:
    await message.answer(
        "<b>Статистика</b>\n\nВыберите, какие результаты хотите посмотреть:",
        reply_markup=stats_menu(),
    )


@router.callback_query(StatsCallback.filter(F.action.in_({"all", "one"})))
async def stats_show(
    callback: CallbackQuery, callback_data: StatsCallback, session: AsyncSession
) -> None:
    await callback.answer()
    repo = VisitRepository(session)
    link = (
        await LinkRepository(session).get(callback_data.link_id)
        if callback_data.link_id
        else None
    )
    if callback_data.link_id and not link:
        await callback.message.answer("Эта ссылка больше не существует.")
        return
    link_repo = LinkRepository(session)
    await callback.message.answer(
        stats_text(
            link.name if link else "Общая статистика",
            await repo.summary(link.id if link else None),
            await link_repo.downloads_total(link.id if link else None),
        ),
        reply_markup=report_button(link.id if link else 0),
    )


@router.callback_query(StatsCallback.filter(F.action == "links"))
async def stats_links(
    callback: CallbackQuery,
    callback_data: StatsCallback,
    session: AsyncSession,
) -> None:
    await callback.answer()
    page = max(callback_data.page, 0)
    items, total = await LinkRepository(session).page(page)
    buttons = [
        [
            callback_button(
                link.name, StatsCallback(action="one", link_id=link.id).pack()
            )
        ]
        for link in items
    ]
    navigation = []
    if page > 0:
        navigation.append(
            callback_button(
                "← Назад", StatsCallback(action="links", page=page - 1).pack()
            )
        )
    if (page + 1) * 8 < total:
        navigation.append(
            callback_button(
                "Вперёд →", StatsCallback(action="links", page=page + 1).pack()
            )
        )
    if navigation:
        buttons.append(navigation)
    text = (
        "Выберите ссылку, статистику которой хотите посмотреть:"
        if items
        else "У вас пока нет активных ссылок. Создайте первую с помощью команды /add."
    )
    await callback.message.answer(text, reply_markup=inline_keyboard(buttons))


@router.callback_query(StatsCallback.filter(F.action == "report"))
async def stats_report(
    callback: CallbackQuery, callback_data: StatsCallback, session: AsyncSession
) -> None:
    await callback.answer("Формирую отчёт")
    links = await LinkRepository(session).all()
    vr = VisitRepository(session)
    detail = (
        await LinkRepository(session).get(callback_data.link_id)
        if callback_data.link_id
        else None
    )
    summary = await vr.summary(detail.id if detail else None)
    metrics = {x.id: await vr.summary(x.id) for x in links}
    source_stats = []
    for link in links:
        for source in await SourceRepository(session).list_for_link(link.id):
            source_stats.append((source, await vr.source_summary(source.id)))
    channel = await ChannelRepository(session).get()
    visits = await vr.rows(detail.id) if detail else []
    users = await vr.users.by_telegram_ids({visit.telegram_user_id for visit in visits})
    path = create_report(
        summary,
        links,
        metrics,
        channel.title if channel else "не настроен",
        detail,
        visits,
        source_stats,
        users,
        await vr.event_rows(detail.id) if detail else [],
    )
    try:
        await callback.message.answer_document(
            FSInputFile(path),
            caption="Отчёт со статистикой. Скачайте файл и откройте его в браузере.",
        )
    finally:
        path.unlink(missing_ok=True)


@router.message(Command("post"))
async def post(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    delivery_manager: DeliveryBotManager,
) -> None:
    if await DeliveryBotRepository(session).get() is None:
        await message.answer(
            "Сначала подключите бота для подписчиков с помощью команды /bot."
        )
        return
    if delivery_manager.get_bot(message.from_user.id) is None:
        await message.answer(
            "Бот для подписчиков сейчас не запущен. Подключите его заново через /bot."
        )
        return
    if not await ChannelRepository(session).get():
        await message.answer(
            "Сначала подключите канал через /channel. После этого вернитесь к команде /post."
        )
        return
    await state.update_data(content_items=[], buttons=[])
    await state.set_state(PostFlow.content)
    await message.answer(
        "<b>Создадим пост для канала.</b>\n\nПришлите готовый пост: текст, фото, видео, документ или GIF. Можно отправить несколько сообщений в нужном порядке. Когда закончите, нажмите «Готово — добавить кнопки»."
    )


@router.message(PostFlow.content)
async def post_content(
    message: Message,
    state: FSMContext,
    bot: Bot,
    storage: FileStorageService,
    rollback_cleanups: list,
) -> None:
    item = content(message)
    if not item:
        await message.answer(
            "Я не могу использовать сообщение такого типа. Пришлите текст, фото, видео, документ или GIF."
        )
        return
    if item["content_type"] != "text":
        if file_is_too_large(item):
            limit_mb = settings.max_telegram_file_size // (1024 * 1024)
            await message.answer(
                f"Этот файл слишком большой. Пришлите файл размером не больше {limit_mb} МБ."
            )
            return
        try:
            stored = await storage.save_telegram_file(
                bot,
                item["file_id"] or "",
                "posts/pending",
                item["original_filename"] or "post-file",
                item.get("mime_type"),
            )
        except (TelegramAPIError, OSError, ValueError):
            await message.answer(
                "Не удалось сохранить файл. Попробуйте отправить его ещё раз."
            )
            return
        item.update(
            file_id=None,
            relative_path=stored.relative_path,
            original_filename=stored.original_filename,
            mime_type=stored.mime_type,
            file_size=stored.file_size,
        )
        rollback_cleanups.append(lambda: storage.delete(stored.relative_path))
    data = await state.get_data()
    items = data.get("content_items", [])
    items.append(item)
    await state.update_data(content_items=items)
    await message.answer(
        f"Добавлено сообщений: {len(items)}. Пришлите следующее или нажмите «Готово — добавить кнопки».",
        reply_markup=inline_keyboard(
            [
                [
                    callback_button(
                        "Готово — добавить кнопки",
                        PostCallback(action="content_done").pack(),
                    )
                ]
            ]
        ),
    )


@router.callback_query(
    PostCallback.filter(F.action == "content_done"), PostFlow.content
)
async def finish_post_content(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if not (await state.get_data()).get("content_items"):
        await callback.message.answer(
            "Сначала пришлите хотя бы одно сообщение или файл."
        )
        return
    await state.set_state(PostFlow.button_choice)
    await callback.message.answer(
        "Хотите добавить кнопку со ссылкой под постом? Если кнопки не нужны, выберите «Не добавлять кнопку».",
        reply_markup=post_choice(),
    )


@router.callback_query(
    PostCallback.filter(F.action == "button"), PostFlow.button_choice
)
@router.callback_query(PostCallback.filter(F.action == "button"), PostFlow.button_more)
async def post_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    if len(data.get("buttons", [])) >= MAX_POST_BUTTONS:
        await callback.message.answer(
            "Достигнут технический предел Telegram: 100 кнопок. Нажмите «Посмотреть перед публикацией»."
        )
        return
    await state.set_state(PostFlow.button_text)
    await callback.message.answer(
        "Напишите короткий текст для кнопки. Например: «Перейти на сайт» или «Скачать»."
    )


@router.message(PostFlow.button_text)
async def post_button_text(message: Message, state: FSMContext) -> None:
    button_text = (message.text or "").strip()
    if not button_text or len(button_text) > 64:
        await message.answer(
            "Напишите непустой текст длиной до 64 символов. Например: «Открыть сайт»."
        )
        return
    await state.update_data(button_text=button_text)
    await state.set_state(PostFlow.button_url)
    await message.answer(
        "Теперь пришлите ссылку, куда должна вести кнопка. Она должна начинаться с https://"
    )


@router.message(PostFlow.button_url)
async def post_button_url(message: Message, state: FSMContext) -> None:
    try:
        button_url = TypeAdapter(HttpUrl).validate_python(message.text)
    except ValidationError:
        await message.answer(
            "Эта ссылка не подходит. Пришлите полную ссылку, например: https://example.com"
        )
        return
    if button_url.scheme != "https":
        await message.answer(
            "Для безопасности ссылка должна начинаться с https:// Пришлите другую ссылку."
        )
        return
    data = await state.get_data()
    buttons = data["buttons"] + [(data["button_text"], str(button_url))]
    await state.update_data(buttons=buttons)
    await state.set_state(PostFlow.button_more)
    await message.answer(
        "✅ Кнопка добавлена. Можно добавить ещё одну или посмотреть пост перед публикацией.",
        reply_markup=post_more(),
    )


@router.callback_query(
    PostCallback.filter(F.action == "preview"), PostFlow.button_choice
)
@router.callback_query(PostCallback.filter(F.action == "preview"), PostFlow.button_more)
async def preview_post(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    storage: FileStorageService,
) -> None:
    await callback.answer()
    data = await state.get_data()
    buttons = [[url_button(text, url)] for text, url in data["buttons"]]
    await callback.message.answer("Предпросмотр:")
    await send_link_content(
        bot,
        callback.message.chat.id,
        type("C", (), {})(),
        buttons,
        storage,
        content_items=[type("C", (), item)() for item in data.get("content_items", [])],
    )
    await state.set_state(PostFlow.preview)
    await callback.message.answer(
        "Проверьте предпросмотр выше. Если всё верно, нажмите «Да, опубликовать в канал».\n"
        "До этого пост не появится в канале.",
        reply_markup=post_preview(),
    )


@router.callback_query(PostCallback.filter(F.action == "publish"), PostFlow.preview)
async def publish(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    delivery_manager: DeliveryBotManager,
    storage: FileStorageService,
) -> None:
    await callback.answer()
    channel = await ChannelRepository(session).get()
    if not channel:
        await callback.message.answer(
            "Сначала нужно подключить канал. Отправьте /channel, выберите канал, а затем создайте пост заново."
        )
        return
    delivery_bot = delivery_manager.get_bot(callback.from_user.id)
    if delivery_bot is None:
        await callback.message.answer(
            "Бот для подписчиков сейчас не запущен. Подключите его заново через /bot."
        )
        return
    data = await state.get_data()
    buttons = [[url_button(text, url)] for text, url in data["buttons"]]
    try:
        await send_link_content(
            delivery_bot,
            channel.channel_id,
            type("C", (), {})(),
            buttons,
            storage,
            content_items=[
                type("C", (), item)() for item in data.get("content_items", [])
            ],
        )
    except TelegramAPIError:
        await callback.message.answer(
            "Не удалось опубликовать пост. Проверьте права бота."
        )
        return
    await cleanup_post_draft(state, storage)
    await state.clear()
    await callback.message.answer("✅ Готово! Пост опубликован в подключённом канале.")


@router.callback_query(PostCallback.filter(F.action == "restart"), PostFlow.preview)
async def post_restart(
    callback: CallbackQuery, state: FSMContext, storage: FileStorageService
) -> None:
    await callback.answer()
    await cleanup_post_draft(state, storage)
    await state.clear()
    await state.set_state(PostFlow.content)
    await callback.message.answer("Отправьте новое содержимое публикации.")


@router.callback_query(PostCallback.filter(F.action == "cancel"))
async def post_cancel(
    callback: CallbackQuery, state: FSMContext, storage: FileStorageService
) -> None:
    await callback.answer()
    await cleanup_post_draft(state, storage)
    await state.clear()
    await callback.message.answer("Действие отменено.")
