from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    Message,
    ReplyKeyboardRemove,
)
from pydantic import HttpUrl, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.repositories import (
    ChannelRepository,
    LinkRepository,
    SourceRepository,
    VisitRepository,
)
from bot.keyboards.callbacks import (
    AdminCallback,
    ChannelCallback,
    LinkCallback,
    MaterialCallback,
    PostCallback,
    SourceCallback,
    StatsCallback,
)
from bot.keyboards.keyboards import (
    CHANNEL_REQUEST_ID,
    channel_menu,
    channel_selector,
    confirm,
    fallback_files_menu,
    github_preview_keyboard,
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
from bot.services.commands_service import remove_admin_commands
from bot.services.github_service import GithubService
from bot.services.services import (
    AdminInviteService,
    ChannelSetupService,
    LinkService,
    TrafficSourceService,
    send_link_content,
    stats_text,
)
from bot.services.storage_service import FileStorageService
from bot.states.flows import ChannelFlow, PostFlow, SmartLinkFlow, SourceFlow
from bot.utils.admins_json import AdminStore, admin_label
from bot.utils.html_report import create_report
from bot.utils.rich_messages import callback_button, url_button
from config import settings

router = Router(name="admin")


def content(message: Message) -> dict[str, str | None] | None:
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
                "caption": message.html_caption,
                "file_id": value.file_id,
                "original_filename": original_filename,
                "mime_type": getattr(value, "mime_type", None),
            }
    return None


def fallback_file(message: Message, position: int) -> dict[str, str | None] | None:
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
            }
    return None


async def cleanup_link_draft(state: FSMContext, storage: FileStorageService) -> None:
    data = await state.get_data()
    content = data.get("content", {})
    await storage.delete(content.get("relative_path"))
    for file in data.get("fallback_files", []):
        await storage.delete(file.get("relative_path"))


def url(bot_username: str, slug: str) -> str:
    return f"https://t.me/{bot_username}?start={slug}"


@router.message(Command("admin"))
async def admins(message: Message) -> None:
    await message.answer(
        "У каждого пользователя свой кабинет. Помощники не поддерживаются."
    )


@router.callback_query(AdminCallback.filter(F.action == "add"))
async def add_admin(callback: CallbackQuery, bot: Bot, session: AsyncSession) -> None:
    await callback.answer()
    token = await AdminInviteService(session).create(callback.from_user.id)
    me = await bot.get_me()
    await callback.message.answer(
        f"Отправьте человеку одноразовую ссылку (действует 24 часа):\n<code>https://t.me/{me.username}?start=admin_{token}</code>"
    )


@router.callback_query(AdminCallback.filter(F.action == "remove"))
async def choose_admin(callback: CallbackQuery, admin_store: AdminStore) -> None:
    await callback.answer()
    items = await admin_store.list_admins()
    kb = [
        [
            callback_button(
                f"Удалить {admin_label(x)}",
                AdminCallback(action="ask", target_id=x["telegram_id"]).pack(),
            )
        ]
        for x in items
    ]
    await callback.message.answer("Выберите администратора:", reply_markup=kb)


@router.callback_query(AdminCallback.filter(F.action == "ask"))
async def ask_remove_admin(
    callback: CallbackQuery, callback_data: AdminCallback, admin_store: AdminStore
) -> None:
    await callback.answer()
    item = next(
        (
            a
            for a in await admin_store.list_admins()
            if a["telegram_id"] == callback_data.target_id
        ),
        None,
    )
    if not item:
        await callback.message.answer("Администратор уже удалён.")
        return
    kb = [
        [
            callback_button(
                "Да, удалить",
                AdminCallback(action="yes", target_id=callback_data.target_id).pack(),
            ),
            callback_button("Отмена", AdminCallback(action="no").pack()),
        ]
    ]
    await callback.message.answer(f"Удалить {admin_label(item)}?", reply_markup=kb)


@router.callback_query(AdminCallback.filter(F.action == "no"))
async def cancel_remove_admin(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")


@router.callback_query(AdminCallback.filter(F.action == "yes"))
async def remove_admin(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    admin_store: AdminStore,
    bot: Bot,
) -> None:
    await callback.answer()
    ok = await admin_store.remove(callback_data.target_id)
    if ok:
        await remove_admin_commands(bot, callback_data.target_id)
    await callback.message.answer(
        "Администратор удалён."
        if ok
        else "Нельзя удалить единственного администратора."
    )


@router.message(Command("channel"))
async def channel(
    message: Message, session: AsyncSession, bot: Bot, state: FSMContext
) -> None:
    item = await ChannelRepository(session).get()
    if not item:
        await state.set_state(ChannelFlow.value)
        await state.update_data(channel_request_id=CHANNEL_REQUEST_ID)
        await message.answer(
            "<b>Сначала подключите канал.</b>\n\n"
            "1. Откройте свой канал и добавьте этого бота как администратора.\n"
            "2. Разрешите ему публиковать сообщения.\n"
            "3. Вернитесь сюда и нажмите «Выбрать канал» внизу экрана.\n\n"
            "Если канал закрытый, разрешите боту создавать пригласительные ссылки.",
            reply_markup=channel_selector(),
        )
        return
    try:
        status = (
            await bot.get_chat_member(item.channel_id, (await bot.get_me()).id)
        ).status
        checked = (
            "✅ администратор"
            if status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
            else "❌ не администратор"
        )
    except TelegramAPIError:
        checked = "❌ недоступен"
    await message.answer(
        f"<b>Канал</b>\n\nНазвание: {item.title}\nUsername: @{item.username or '—'}\nID: <code>{item.channel_id}</code>\n\nБот: {checked}",
        reply_markup=channel_menu(True),
    )


@router.callback_query(ChannelCallback.filter(F.action.in_({"add", "change"})))
async def channel_input(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ChannelFlow.value)
    await state.update_data(channel_request_id=CHANNEL_REQUEST_ID)
    await callback.message.answer(
        "Добавьте бота в новый канал как администратора и разрешите ему публиковать сообщения. Затем нажмите «Выбрать канал» внизу экрана.",
        reply_markup=channel_selector(),
    )


@router.message(ChannelFlow.value, F.chat_shared)
async def channel_save(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    shared = message.chat_shared
    data = await state.get_data()
    if shared.request_id != data.get("channel_request_id"):
        await message.answer("Это устаревший запрос выбора канала.")
        return

    result = await ChannelSetupService(bot).validate(shared.chat_id)
    if not result.is_valid:
        await state.clear()
        await message.answer(
            "Не получилось подключить канал.\n\n"
            "Проверьте, что бот добавлен в этот канал как администратор и может публиковать сообщения. "
            "Для закрытого канала также разрешите ему создавать пригласительные ссылки. Затем попробуйте ещё раз.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await ChannelRepository(session).save(
        channel_id=shared.chat_id,
        title=result.title or str(shared.chat_id),
        username=result.username,
        invite_url=result.invite_url,
    )
    await state.clear()
    await message.answer(
        "✅ Канал подключён! Теперь отправьте /add, чтобы создать ссылку и выдать материал подписчикам.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ChannelFlow.value, F.text == "Отмена")
async def cancel_channel_selection(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())


@router.message(ChannelFlow.value)
async def channel_selection_hint(message: Message) -> None:
    await message.answer(
        "Нажмите кнопку «Выбрать канал» внизу экрана. Telegram покажет список ваших каналов."
    )


@router.callback_query(ChannelCallback.filter(F.action == "delete"))
async def channel_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    await ChannelRepository(session).delete()
    await callback.message.answer(
        "Канал отключён. Пока не подключите новый канал через /channel, ссылки не смогут проверять подписку."
    )


@router.message(Command("add"))
async def add_link(message: Message, state: FSMContext) -> None:
    await state.set_state(SmartLinkFlow.name)
    await message.answer(
        "<b>Создадим ссылку для выдачи материала.</b>\n\n"
        "Напишите её понятное название для себя. Его увидите только вы.\n"
        "Например: «Реклама у блогера» или «Подарок с сайта»."
    )


@router.message(SmartLinkFlow.name)
async def link_name(message: Message, state: FSMContext) -> None:
    if not message.text or len(message.text) > 128:
        await message.answer("Введите название до 128 символов.")
        return
    await state.update_data(name=message.text)
    await state.set_state(SmartLinkFlow.message)
    await message.answer(
        "Теперь напишите сообщение для человека. Он увидит его после подписки вместе с материалом.\n\n"
        "Например: «Спасибо за подписку! Вот ваш подарок»."
    )


@router.message(SmartLinkFlow.message)
async def link_message(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Сообщение должно быть текстом. Отправьте его ещё раз.")
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
        await message.answer("Поддерживаются текст, фото, видео, документ и animation.")
        return
    if item["content_type"] != "text":
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
    item.update(github_owner=None, github_repo=None, github_url=None)
    await state.update_data(content=item)
    await show_link_preview(message, state, bot, storage)


@router.message(SmartLinkFlow.github_url)
async def github_url_input(message: Message, state: FSMContext, bot: Bot) -> None:
    validation = await GithubService(
        settings.max_github_archive_size
    ).validate_repository(message.text or "")
    if validation.status == "invalid_url":
        await message.answer(
            "Укажите ссылку на корень репозитория: https://github.com/owner/repository"
        )
        return
    if validation.status == "not_found":
        await message.answer(
            "Репозиторий не найден. Проверьте ссылку и попробуйте снова."
        )
        return
    if validation.status == "private":
        await message.answer(
            "Сейчас поддерживаются только публичные GitHub-репозитории."
        )
        return
    if validation.status != "ok" or not validation.repository:
        await message.answer("Не удалось проверить GitHub. Попробуйте немного позже.")
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
        "Теперь загрузите резервные файлы.\n\n"
        "Они будут отправлены пользователю только в том случае, если бот не сможет скачать "
        "актуальный ZIP-архив с GitHub.",
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
    file = fallback_file(message, len(files))
    if not file:
        await message.answer(
            "Отправьте фото, видео, документ или animation. Затем нажмите «Готово»."
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
        await callback.message.answer("Добавьте хотя бы один резервный файл.")
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
    material = data["content"]
    kind = material["content_type"]
    if kind == "github_repository":
        await message.answer(
            "<b>Предпросмотр умной ссылки</b>\n\n"
            f"<b>Название:</b> {data['name']}\n\n"
            f"<b>Сообщение:</b>\n{data['message_text']}\n\n"
            f"<b>Материал:</b> GitHub — {material['github_owner']}/{material['github_repo']}\n"
            f"<b>Резервных файлов:</b> {len(data.get('fallback_files', []))}",
            reply_markup=github_preview_keyboard(),
        )
    elif kind == "none":
        await message.answer(
            "<b>Предпросмотр умной ссылки</b>\n\n"
            f"<b>Название:</b> {data['name']}\n\n{data['message_text']}"
        )
    else:
        await message.answer("Предпросмотр сообщения:")
        preview = {**material, "message_text": data["message_text"]}
        await send_link_content(
            bot, message.chat.id, type("C", (), preview)(), storage=storage
        )
    await state.set_state(SmartLinkFlow.preview)
    await message.answer(
        "Проверьте сообщение выше. Если всё верно, нажмите «Сохранить и получить ссылку».\n"
        "До нажатия этой кнопки ссылка ещё не создана.",
        reply_markup=link_preview(),
    )


@router.callback_query(LinkCallback.filter(F.action == "preview_github"))
async def preview_github_download(callback: CallbackQuery) -> None:
    await callback.answer("Сначала сохраните умную ссылку.")


@router.callback_query(LinkCallback.filter(F.action == "save"), SmartLinkFlow.preview)
async def save_link(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    storage: FileStorageService,
    rollback_cleanups: list,
) -> None:
    await callback.answer()
    data = await state.get_data()
    content = data.get("content", {})
    if content.get("relative_path"):
        rollback_cleanups.append(lambda: storage.delete(content["relative_path"]))
    for file in data.get("fallback_files", []):
        if file.get("relative_path"):
            rollback_cleanups.append(
                lambda path=file["relative_path"]: storage.delete(path)
            )
    content_data = {**data["content"], "message_text": data["message_text"]}
    link = await LinkService(session).create(
        data["name"],
        content_data,
        callback.from_user.id,
        data.get("fallback_files"),
    )
    me = await bot.get_me()
    target = url(me.username, link.slug)
    await state.clear()
    await callback.message.answer(
        f"Умная ссылка создана.\n\nНазвание: {link.name}\nСсылка:\n<code>{target}</code>",
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
    await callback.message.answer("Введите новое название умной ссылки.")


@router.message(Command("links"))
async def links(message: Message, session: AsyncSession) -> None:
    await show_links(message, session, 0)


async def show_links(message: Message, session: AsyncSession, page: int) -> None:
    items, total = await LinkRepository(session).page(page)
    await message.answer(
        f"<b>Умные ссылки</b>\nВсего активных: {total}\nВыберите ссылку:",
        reply_markup=links_page(items, page),
    )


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
    bot: Bot,
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(callback_data.link_id, True)
    if not link:
        await callback.message.answer("Эта ссылка больше не существует.")
        return
    data = await VisitRepository(session).summary(link.id)
    me = await bot.get_me()
    material = (
        f"\n\nТип: GitHub repository\nРепозиторий: {link.github_owner}/{link.github_repo}"
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
        reply_markup=link_actions(link.id, url(me.username, link.slug)),
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
    lines = ["<b>Источники трафика</b>"]
    buttons = []
    for source in sources:
        summary = await visits.source_summary(source.id)
        status = "" if source.is_active else " (отключён)"
        lines.append(f"\n{source.name}{status}\nПереходов: {summary['total_visits']}")
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
    await callback.message.answer("\n".join(lines), reply_markup=buttons)


@router.callback_query(SourceCallback.filter(F.action == "add"))
async def add_source_start(
    callback: CallbackQuery, callback_data: SourceCallback, state: FSMContext
) -> None:
    await callback.answer()
    await state.set_state(SourceFlow.name)
    await state.update_data(source_link_id=callback_data.link_id, source_mode="add")
    await callback.message.answer("Введите название источника, например Instagram.")


@router.message(SourceFlow.name)
async def add_source_name(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    if not message.text or len(message.text) > 128:
        await message.answer("Введите название источника до 128 символов.")
        return
    source_data = await state.get_data()
    if source_data.get("source_mode") == "rename":
        source = await SourceRepository(session).get(source_data.get("source_id"))
        if not source:
            await state.clear()
            await message.answer("Источник больше не существует.")
            return
        source.name = message.text
        await state.clear()
        await message.answer("Название источника изменено. Ссылка осталась прежней.")
        return
    link_id = source_data.get("source_link_id")
    link = await LinkRepository(session).get(link_id, active_only=True)
    if not link:
        await state.clear()
        await message.answer("Эта ссылка больше не существует.")
        return
    source = await TrafficSourceService(session).create(link.id, message.text)
    me = await bot.get_me()
    target = url(me.username, source.token)
    await state.clear()
    await message.answer(
        f"Источник создан.\n\nНазвание: {source.name}\nСсылка:\n<code>{target}</code>",
        reply_markup=source_actions(source.id, link.id, target),
    )


@router.callback_query(SourceCallback.filter(F.action == "show"))
async def show_source(
    callback: CallbackQuery,
    callback_data: SourceCallback,
    session: AsyncSession,
    bot: Bot,
) -> None:
    await callback.answer()
    source = await SourceRepository(session).get(callback_data.source_id)
    if not source:
        await callback.message.answer("Источник больше не существует.")
        return
    summary = await VisitRepository(session).source_summary(source.id)
    me = await bot.get_me()
    await callback.message.answer(
        stats_text(source.name, summary),
        reply_markup=source_actions(
            source.id, source.smart_link_id, url(me.username, source.token)
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
    callback: CallbackQuery, callback_data: SourceCallback, state: FSMContext
) -> None:
    await callback.answer()
    await state.set_state(SourceFlow.name)
    await state.update_data(source_mode="rename", source_id=callback_data.source_id)
    await callback.message.answer("Введите новое название источника.")


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
        "Источник отключён. Историческая статистика сохранена."
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
        f"Удалить умную ссылку «{link.name}»?", reply_markup=confirm("delete", link.id)
    )


@router.callback_query(LinkCallback.filter(F.action == "delete"))
async def link_delete(
    callback: CallbackQuery, callback_data: LinkCallback, session: AsyncSession
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(callback_data.link_id)
    if not link:
        await callback.message.answer("Эта ссылка больше не существует.")
        return
    await LinkRepository(session).deactivate(link)
    await callback.message.answer(
        "Ссылка отключена. Историческая статистика сохранена."
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
async def stats_links(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    items, _ = await LinkRepository(session).page(0)
    kb = [
        [callback_button(x.name, StatsCallback(action="one", link_id=x.id).pack())]
        for x in items
    ]
    await callback.message.answer("Выберите умную ссылку:", reply_markup=kb)


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
            FSInputFile(path), caption="HTML-отчёт со статистикой"
        )
    finally:
        path.unlink(missing_ok=True)


@router.message(Command("post"))
async def post(message: Message, state: FSMContext) -> None:
    await state.set_state(PostFlow.content)
    await message.answer(
        "<b>Создадим пост для канала.</b>\n\nПришлите сюда текст, фото, видео, документ или GIF. "
        "Сначала вы увидите предпросмотр, и только потом сможете опубликовать пост."
    )


@router.message(PostFlow.content)
async def post_content(message: Message, state: FSMContext) -> None:
    item = content(message)
    if not item:
        await message.answer("Поддерживаются текст, фото, видео, документ и animation.")
        return
    await state.update_data(content=item, buttons=[])
    await state.set_state(PostFlow.button_choice)
    await message.answer(
        "Хотите добавить кнопку со ссылкой под постом? Например, «Открыть сайт».\n"
        "Если кнопка не нужна, выберите «Не добавлять кнопку».",
        reply_markup=post_choice(),
    )


@router.callback_query(
    PostCallback.filter(F.action == "button"), PostFlow.button_choice
)
@router.callback_query(PostCallback.filter(F.action == "button"), PostFlow.button_more)
async def post_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(PostFlow.button_text)
    await callback.message.answer(
        "Напишите короткий текст для кнопки. Например: «Перейти на сайт» или «Скачать»."
    )


@router.message(PostFlow.button_text)
async def post_button_text(message: Message, state: FSMContext) -> None:
    if not message.text or len(message.text) > 64:
        await message.answer("Введите текст до 64 символов.")
        return
    await state.update_data(button_text=message.text)
    await state.set_state(PostFlow.button_url)
    await message.answer(
        "Теперь пришлите ссылку, куда должна вести кнопка. Она должна начинаться с https://"
    )


@router.message(PostFlow.button_url)
async def post_button_url(message: Message, state: FSMContext) -> None:
    try:
        TypeAdapter(HttpUrl).validate_python(message.text)
    except ValidationError:
        await message.answer("Некорректный URL.")
        return
    data = await state.get_data()
    buttons = data["buttons"] + [(data["button_text"], message.text)]
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
async def preview_post(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    data = await state.get_data()
    buttons = [[url_button(text, url)] for text, url in data["buttons"]]
    await callback.message.answer("Предпросмотр:")
    await send_link_content(
        bot, callback.message.chat.id, type("C", (), data["content"])(), buttons
    )
    await state.set_state(PostFlow.preview)
    await callback.message.answer(
        "Проверьте предпросмотр выше. Если всё верно, нажмите «Да, опубликовать в канал».\n"
        "До этого пост не появится в канале.",
        reply_markup=post_preview(),
    )


@router.callback_query(PostCallback.filter(F.action == "publish"), PostFlow.preview)
async def publish(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    await callback.answer()
    channel = await ChannelRepository(session).get()
    if not channel:
        await callback.message.answer(
            "Сначала нужно подключить канал. Отправьте /channel, выберите канал, а затем создайте пост заново."
        )
        return
    data = await state.get_data()
    buttons = [[url_button(text, url)] for text, url in data["buttons"]]
    try:
        await send_link_content(
            bot, channel.channel_id, type("C", (), data["content"])(), buttons
        )
    except TelegramAPIError:
        await callback.message.answer(
            "Не удалось опубликовать пост. Проверьте права бота."
        )
        return
    await state.clear()
    await callback.message.answer("✅ Готово! Пост опубликован в подключённом канале.")


@router.callback_query(PostCallback.filter(F.action == "restart"), PostFlow.preview)
async def post_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(PostFlow.content)
    await callback.message.answer("Отправьте новое содержимое публикации.")


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
            SmartLinkFlow.github_url.state,
            SmartLinkFlow.github_fallbacks.state,
            SmartLinkFlow.preview.state,
        }
        if current_state in smart_link_states:
            await cleanup_link_draft(state, storage)
        await state.clear()
        if current_state == ChannelFlow.value.state:
            await message.answer(
                "Действие отменено.", reply_markup=ReplyKeyboardRemove()
            )
            return
        await message.answer("Действие отменено.")
    else:
        await message.answer(
            "Сейчас нечего отменять. Начните с /channel, чтобы подключить канал, или с /add, чтобы создать ссылку."
        )


@router.callback_query(PostCallback.filter(F.action == "cancel"))
async def post_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.answer("Действие отменено.")
