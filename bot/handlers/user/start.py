import hashlib
import logging
from time import monotonic

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.repositories import (
    ChannelConnectTokenRepository,
    ChannelRepository,
    ContentItemRepository,
    FallbackFileRepository,
    LinkRepository,
    ResourceRepository,
    SourceRepository,
    VisitRepository,
)
from bot.keyboards.callbacks import (
    LinkCallback,
    ResourceCallback,
    SubscriptionCallback,
)
from bot.keyboards.keyboards import (
    CHANNEL_REQUEST_ID,
    channel_selector,
    subscribe_keyboard,
)
from bot.services.github_service import GithubService
from bot.services.services import (
    ChannelSetupService,
    SubscriptionService,
    send_link_content,
    send_stored_file,
)
from bot.services.storage_service import FileStorageService
from bot.states.flows import DeliveryChannelFlow
from bot.utils.rich_messages import inline_keyboard, url_button
from config import settings

log = logging.getLogger(__name__)

GITHUB_DOWNLOAD_COOLDOWN = 30.0
_github_download_started: dict[int, float] = {}


def _channel_url(channel: object | None) -> str | None:
    if not channel:
        return None
    username = getattr(channel, "username", None)
    return (
        f"https://t.me/{username}" if username else getattr(channel, "invite_url", None)
    )


async def _start_link(
    message: Message,
    payload: str,
    bot: Bot,
    session: AsyncSession,
    storage: FileStorageService,
    delivery_owner_id: int,
) -> None:
    source = await SourceRepository(session).by_token(payload)
    if source and not source.is_active:
        await message.answer(
            "Эта ссылка больше не работает. Попросите у автора новую ссылку."
        )
        return
    link = (
        await LinkRepository(session).get(
            source.smart_link_id, active_only=True, scoped=False
        )
        if source
        else await LinkRepository(session).by_slug(payload)
    )
    if not link or not link.is_active or link.created_by != delivery_owner_id:
        await message.answer(
            "Эта ссылка больше не работает. Попросите у автора новую ссылку."
        )
        return
    subscribed = await SubscriptionService(bot, session, link.created_by).check(
        message.from_user.id
    )
    visits = VisitRepository(session)
    await visits.touch(
        link.id,
        message.from_user,
        subscribed,
        source_id=source.id if source else None,
    )
    if subscribed is None:
        await message.answer(
            "Не удалось проверить подписку. Подождите немного и откройте ссылку ещё раз."
        )
        return
    if source:
        await visits.touch_source(source.id, message.from_user.id, subscribed)
    if subscribed:
        try:
            await send_link_content(
                bot,
                message.chat.id,
                link,
                storage=storage,
                content_items=(
                    await ContentItemRepository(session).list_for_link(link.id)
                )
                or None,
                resources=(await ResourceRepository(session).list_for_link(link.id))
                or None,
            )
        except FileNotFoundError:
            log.error("Smart-link file is missing: link_id=%s", link.id)
            await message.answer(
                "Материал временно недоступен. Попробуйте получить его немного позже."
            )
        return
    url = _channel_url(await ChannelRepository(session).get(link.created_by))
    if not url:
        await message.answer(
            "Канал для подписки временно недоступен. Попробуйте открыть ссылку немного позже."
        )
        return
    await message.answer(
        "Чтобы получить материал:\n\n1. Нажмите «Подписаться на канал».\n2. Вступите в канал.\n3. Вернитесь сюда и нажмите «Проверить подписку».\n\nПосле проверки бот сразу отправит материал.",
        reply_markup=subscribe_keyboard(url, link.id, source.id if source else 0),
    )


async def start_channel_connection(
    message: Message,
    token: str,
    session: AsyncSession,
    state: FSMContext,
    delivery_owner_id: int,
) -> None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connect_token = await ChannelConnectTokenRepository(session).get_valid(token_hash)
    if (
        connect_token is None
        or connect_token.owner_id != message.from_user.id
        or connect_token.owner_id != delivery_owner_id
    ):
        await message.answer(
            "Ссылка для подключения канала недействительна. Вернитесь в бот настроек и отправьте /channel ещё раз."
        )
        return
    await state.set_state(DeliveryChannelFlow.value)
    await state.update_data(
        connect_token_hash=token_hash,
        channel_request_id=CHANNEL_REQUEST_ID,
    )
    await message.answer(
        "<b>Подключение канала</b>\n\n"
        "1. Сначала добавьте этого бота в канал как администратора.\n"
        "2. Разрешите ему публиковать сообщения. Для закрытого канала также разрешите создавать пригласительные ссылки.\n"
        "3. Нажмите «Выбрать канал» ниже и выберите нужный канал.",
        reply_markup=channel_selector(),
    )


async def start(
    message: Message,
    command: CommandObject,
    bot: Bot,
    session: AsyncSession,
    storage: FileStorageService,
    state: FSMContext,
    delivery_owner_id: int,
) -> None:
    payload = command.args or ""
    if payload.startswith("connect_"):
        await start_channel_connection(
            message, payload[8:], session, state, delivery_owner_id
        )
        return
    if payload:
        await _start_link(message, payload, bot, session, storage, delivery_owner_id)
        return
    await message.answer(
        "Этот бот выдаёт материалы по специальным ссылкам. Откройте ссылку, которую прислал автор материала."
    )


async def save_connected_channel(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    delivery_owner_id: int,
) -> None:
    data = await state.get_data()
    if message.chat_shared.request_id != data.get("channel_request_id"):
        await message.answer(
            "Эта кнопка устарела. Вернитесь в бот настроек и отправьте /channel ещё раз."
        )
        return
    token_hash = data.get("connect_token_hash")
    connect_tokens = ChannelConnectTokenRepository(session)
    connect_token = (
        await connect_tokens.get_valid(token_hash)
        if isinstance(token_hash, str)
        else None
    )
    if (
        connect_token is None
        or connect_token.owner_id != message.from_user.id
        or connect_token.owner_id != delivery_owner_id
    ):
        await state.clear()
        await message.answer(
            "Время подключения истекло. Вернитесь в бот настроек и отправьте /channel ещё раз.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    result = await ChannelSetupService(bot).validate(message.chat_shared.chat_id)
    if not result.is_valid:
        await state.clear()
        await message.answer(
            "Не получилось подключить канал. Проверьте, что этот бот добавлен в канал как администратор и может публиковать сообщения. Для закрытого канала также нужно право создавать пригласительные ссылки.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    channels = ChannelRepository(session)
    existing = await channels.by_channel_id(message.chat_shared.chat_id)
    if existing and existing.owner_id != connect_token.owner_id:
        await state.clear()
        await message.answer(
            "Этот канал уже подключён к другому кабинету.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await channels.save_for_owner(
        connect_token.owner_id,
        channel_id=message.chat_shared.chat_id,
        title=result.title or str(message.chat_shared.chat_id),
        username=result.username,
        invite_url=result.invite_url,
    )
    await connect_tokens.consume(connect_token)
    await state.clear()
    await message.answer(
        "✅ Канал подключён. Вернитесь в бот настроек и отправьте /add, чтобы создать первую ссылку.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def cancel_channel_selection(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Подключение канала отменено.", reply_markup=ReplyKeyboardRemove()
    )


async def channel_selection_hint(message: Message) -> None:
    await message.answer("Нажмите кнопку «Выбрать канал» внизу экрана.")


async def check_subscription(
    callback: CallbackQuery,
    callback_data: SubscriptionCallback,
    bot: Bot,
    session: AsyncSession,
    storage: FileStorageService,
    delivery_owner_id: int,
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(
        callback_data.link_id, active_only=True, scoped=False
    )
    if not link or link.created_by != delivery_owner_id:
        await callback.message.answer(
            "Эта ссылка больше не работает. Попросите у автора новую ссылку."
        )
        return
    subscribed = await SubscriptionService(bot, session, link.created_by).check(
        callback.from_user.id
    )
    if subscribed is None:
        await callback.message.answer(
            "Не удалось проверить подписку. Подождите несколько секунд и нажмите кнопку ещё раз."
        )
        return
    if not subscribed:
        await callback.message.answer(
            "Подписка пока не найдена. Откройте канал по кнопке выше, вступите в него, затем вернитесь сюда и нажмите «Проверить подписку»."
        )
        return
    source_id = callback_data.source_id or None
    if source_id:
        source = await SourceRepository(session).get(source_id, scoped=False)
        if not source or source.smart_link_id != link.id:
            source_id = None
    await VisitRepository(session).mark_subscribed(
        link.id, callback.from_user.id, source_id
    )
    try:
        await send_link_content(
            bot,
            callback.message.chat.id,
            link,
            storage=storage,
            content_items=(await ContentItemRepository(session).list_for_link(link.id))
            or None,
            resources=(await ResourceRepository(session).list_for_link(link.id))
            or None,
        )
    except FileNotFoundError:
        log.error("Smart-link file is missing: link_id=%s", link.id)
        await callback.message.answer(
            "Материал временно недоступен. Сообщите об этом автору ссылки или попробуйте позже."
        )


async def download_github_archive(
    callback: CallbackQuery,
    callback_data: LinkCallback,
    bot: Bot,
    session: AsyncSession,
    storage: FileStorageService,
    delivery_owner_id: int,
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(
        callback_data.link_id, active_only=True, scoped=False
    )
    if (
        not link
        or link.created_by != delivery_owner_id
        or link.content_type != "github_repository"
        or not link.github_owner
        or not link.github_repo
        or not link.github_url
    ):
        await callback.message.answer(
            "Эта ссылка больше не работает. Попросите у автора новую ссылку."
        )
        return

    subscribed = await SubscriptionService(bot, session, link.created_by).check(
        callback.from_user.id
    )
    if subscribed is None:
        await callback.message.answer(
            "Сейчас не получилось проверить подписку. Подождите несколько секунд и попробуйте скачать архив ещё раз."
        )
        return
    if not subscribed:
        await callback.message.answer(
            "Сначала откройте исходную ссылку, подпишитесь на канал и нажмите кнопку получения материала. После этого архив станет доступен."
        )
        return

    now = monotonic()
    last_started = _github_download_started.get(callback.from_user.id, 0.0)
    if now - last_started < GITHUB_DOWNLOAD_COOLDOWN:
        wait_seconds = int(GITHUB_DOWNLOAD_COOLDOWN - (now - last_started)) + 1
        await callback.message.answer(
            f"Архив уже готовится или был скачан недавно. Подождите {wait_seconds} сек. и попробуйте снова."
        )
        return
    _github_download_started[callback.from_user.id] = now
    if len(_github_download_started) > 10_000:
        cutoff = now - GITHUB_DOWNLOAD_COOLDOWN
        recent = {
            user_id: started
            for user_id, started in _github_download_started.items()
            if started >= cutoff
        }
        _github_download_started.clear()
        _github_download_started.update(recent)

    progress = await callback.message.answer("Скачиваю свежий архив с GitHub…")
    archive = await GithubService(
        settings.max_telegram_file_size
    ).download_current_archive(link.github_owner, link.github_repo)
    if archive.status != "ok" or not archive.path:
        reason = (
            "⚠️ Актуальный архив проекта слишком большой для автоматической отправки через Telegram."
            if archive.status == "too_large"
            else "⚠️ Не удалось получить актуальный ZIP-архив с GitHub."
        )
        await _send_github_fallback(
            callback, link, session, bot, storage, progress, reason, archive.status
        )
        return

    try:
        await bot.send_document(
            callback.message.chat.id,
            FSInputFile(
                archive.path, filename=f"{link.github_owner}-{link.github_repo}.zip"
            ),
        )
        await LinkRepository(session).increment_downloads(link)
        await progress.delete()
    except TelegramAPIError as exc:
        log.warning(
            "GitHub ZIP Telegram delivery failed for smart_link_id=%s: %s", link.id, exc
        )
        await _send_github_fallback(
            callback,
            link,
            session,
            bot,
            storage,
            progress,
            "⚠️ Не удалось отправить актуальный ZIP-архив с GitHub через Telegram.",
            "telegram_delivery_failed",
        )
    finally:
        archive.path.unlink(missing_ok=True)


async def download_resource_github_archive(
    callback: CallbackQuery,
    callback_data: ResourceCallback,
    bot: Bot,
    session: AsyncSession,
    delivery_owner_id: int,
) -> None:
    resource = await ResourceRepository(session).get(callback_data.resource_id)
    if (
        resource is None
        or resource.resource_type != "github"
        or resource.smart_link.created_by != delivery_owner_id
        or not resource.smart_link.is_active
    ):
        await callback.answer("Этот материал больше недоступен.", show_alert=True)
        return
    await callback.answer()
    progress = await callback.message.answer("Скачиваю свежий архив с GitHub…")
    archive = await GithubService(
        settings.max_telegram_file_size
    ).download_current_archive(resource.github_owner or "", resource.github_repo or "")
    if archive.status != "ok" or not archive.path:
        await progress.edit_text(
            "Не удалось получить архив: он недоступен или превышает допустимый размер."
        )
        return
    try:
        await bot.send_document(
            callback.message.chat.id,
            FSInputFile(
                archive.path,
                filename=f"{resource.github_owner}-{resource.github_repo}.zip",
            ),
        )
        await progress.edit_text("✅ Готово! Свежий архив отправлен сообщением выше.")
    except TelegramAPIError:
        await progress.edit_text("Не удалось отправить архив через Telegram.")
    finally:
        archive.path.unlink(missing_ok=True)


async def _send_github_fallback(
    callback: CallbackQuery,
    link: object,
    session: AsyncSession,
    bot: Bot,
    storage: FileStorageService,
    progress: Message,
    reason: str,
    failure_type: str,
) -> None:
    files = await FallbackFileRepository(session).list_for_link(link.id)
    log.warning(
        "GitHub archive download failed, using fallback files: smart_link_id=%s repository=%s/%s reason=%s",
        link.id,
        link.github_owner,
        link.github_repo,
        failure_type,
    )
    if not files:
        await progress.edit_text(
            "Сейчас не удалось получить архив с GitHub. Попробуйте немного позже."
        )
        return

    await progress.edit_text(
        f"{reason}\n\n"
        "Отправляю сохранённую резервную версию файлов. Обратите внимание: эти файлы "
        "могли устареть с момента их загрузки. Для получения самой актуальной версии "
        "проекта рекомендуем скачать ZIP-архив непосредственно с GitHub."
    )
    sent_count = 0
    for file in files:
        try:
            await send_stored_file(bot, callback.message.chat.id, file, storage)
            sent_count += 1
        except (TelegramAPIError, FileNotFoundError) as exc:
            log.warning(
                "Fallback file delivery failed for smart_link_id=%s: %s", link.id, exc
            )

    if sent_count:
        await LinkRepository(session).increment_downloads(link)
    await callback.message.answer(
        "Свежую версию проекта также можно скачать напрямую с GitHub:",
        reply_markup=inline_keyboard(
            [[url_button("Открыть проект на GitHub", link.github_url)]]
        ),
    )


def create_delivery_router() -> Router:
    router = Router(name="delivery")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(start, CommandStart())
    router.message.register(
        save_connected_channel, DeliveryChannelFlow.value, F.chat_shared
    )
    router.message.register(
        cancel_channel_selection, DeliveryChannelFlow.value, F.text == "Отмена"
    )
    router.message.register(channel_selection_hint, DeliveryChannelFlow.value)
    router.callback_query.register(check_subscription, SubscriptionCallback.filter())
    router.callback_query.register(
        download_github_archive,
        LinkCallback.filter(F.action == "download_github"),
    )
    router.callback_query.register(
        download_resource_github_archive, ResourceCallback.filter()
    )
    return router
