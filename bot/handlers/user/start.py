import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.repositories import (
    ChannelRepository,
    FallbackFileRepository,
    LinkRepository,
    SourceRepository,
    VisitRepository,
)
from bot.keyboards.callbacks import LinkCallback, SubscriptionCallback
from bot.keyboards.keyboards import subscribe_keyboard
from bot.services.github_service import GithubService
from bot.services.services import (
    SubscriptionService,
    send_link_content,
    send_stored_file,
)
from bot.services.storage_service import FileStorageService
from bot.utils.rich_messages import answer_rich, url_button
from config import settings

log = logging.getLogger(__name__)
router = Router(name="user_start")


def _channel_url(channel: object | None) -> str | None:
    if not channel:
        return None
    username = getattr(channel, "username", None)
    return (
        f"https://t.me/{username}" if username else getattr(channel, "invite_url", None)
    )


async def _user_bio(bot: Bot, user_id: int) -> str | None:
    try:
        return getattr(await bot.get_chat(user_id), "bio", None)
    except TelegramAPIError:
        # A bio is not always available through the Bot API; profile storage is optional.
        return None


async def _start_link(
    message: Message,
    payload: str,
    bot: Bot,
    session: AsyncSession,
    storage: FileStorageService,
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
    if not link or not link.is_active:
        await message.answer(
            "Эта ссылка больше не работает. Попросите у автора новую ссылку."
        )
        return
    bio = await _user_bio(bot, message.from_user.id)
    subscribed = await SubscriptionService(bot, session, link.created_by).check(
        message.from_user.id
    )
    visits = VisitRepository(session)
    await visits.touch(
        link.id,
        message.from_user,
        subscribed,
        bio=bio,
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
            await send_link_content(bot, message.chat.id, link, storage=storage)
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
    await answer_rich(
        message,
        "Чтобы получить материал:\n\n1. Нажмите «Подписаться на канал».\n2. Вступите в канал.\n3. Вернитесь сюда и нажмите «Проверить подписку».\n\nПосле проверки бот сразу отправит материал.",
        subscribe_keyboard(url, link.id, source.id if source else 0),
    )


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
    bot: Bot,
    session: AsyncSession,
    storage: FileStorageService,
) -> None:
    payload = command.args or ""
    if payload:
        await _start_link(message, payload, bot, session, storage)
        return
    await message.answer(
        "<b>Здравствуйте! Это ваш кабинет для выдачи материалов за подписку.</b>\n\n"
        "С чего начать:\n"
        "1. Добавьте бота администратором в свой канал.\n"
        "2. Отправьте /channel и подключите этот канал.\n"
        "3. Отправьте /add, создайте материал и получите готовую ссылку.\n\n"
        "По этой ссылке люди подпишутся на канал и получат материал.\n\n"
        "Когда понадобится: /links — ссылки, /stats — статистика, /post — публикация в канал.\n"
        "Если запутались во время создания, отправьте /cancel."
    )


@router.callback_query(SubscriptionCallback.filter())
async def check_subscription(
    callback: CallbackQuery,
    callback_data: SubscriptionCallback,
    bot: Bot,
    session: AsyncSession,
    storage: FileStorageService,
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(callback_data.link_id, active_only=True)
    if not link:
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
        source = await SourceRepository(session).get(source_id, active_only=True)
        if not source or source.smart_link_id != link.id:
            source_id = None
    await VisitRepository(session).mark_subscribed(
        link.id, callback.from_user.id, source_id
    )
    try:
        await send_link_content(bot, callback.message.chat.id, link, storage=storage)
    except FileNotFoundError:
        log.error("Smart-link file is missing: link_id=%s", link.id)
        await callback.message.answer("Не удалось получить файл. Попробуйте позже.")


@router.callback_query(LinkCallback.filter(F.action == "download_github"))
async def download_github_archive(
    callback: CallbackQuery,
    callback_data: LinkCallback,
    bot: Bot,
    session: AsyncSession,
    storage: FileStorageService,
) -> None:
    await callback.answer()
    link = await LinkRepository(session).get(callback_data.link_id, active_only=True)
    if (
        not link
        or link.content_type != "github_repository"
        or not link.github_owner
        or not link.github_repo
        or not link.github_url
    ):
        await callback.message.answer("Эта ссылка больше не существует.")
        return

    subscribed = await SubscriptionService(bot, session, link.created_by).check(
        callback.from_user.id
    )
    if not subscribed:
        await callback.message.answer(
            "Сначала подпишитесь на канал и нажмите «Проверить подписку». После этого архив станет доступен."
        )
        return

    progress = await callback.message.answer("Подготавливаю актуальный архив...")
    archive = await GithubService(
        settings.max_github_archive_size
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
        await progress.edit_text("Архив отправлен.")
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
    await answer_rich(
        callback.message,
        "Актуальная версия проекта доступна на GitHub:",
        [[url_button("Скачать актуальную версию на GitHub", link.github_url)]],
    )
