import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, User
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.repositories import (
    ChannelRepository,
    FallbackFileRepository,
    InviteRepository,
    LinkRepository,
    SourceRepository,
    VisitRepository,
)
from bot.keyboards.keyboards import github_download_keyboard
from bot.services.storage_service import FileStorageService
from bot.utils.rich_messages import RichButtons, footer_text, rich_message

log = logging.getLogger(__name__)


def slug() -> str:
    return secrets.token_urlsafe(9).replace("-", "A").replace("_", "B")[:12]


@dataclass(frozen=True)
class ChannelValidationResult:
    is_valid: bool
    title: str | None = None
    username: str | None = None
    invite_url: str | None = None


class ChannelSetupService:
    """Validates a selected channel before it becomes the active configuration."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def validate(self, channel_id: int) -> ChannelValidationResult:
        try:
            chat = await self.bot.get_chat(channel_id)
            if chat.type != ChatType.CHANNEL:
                return ChannelValidationResult(False)

            me = await self.bot.get_me()
            member = await self.bot.get_chat_member(channel_id, me.id)
            is_creator = member.status == ChatMemberStatus.CREATOR
            is_admin = is_creator or member.status == ChatMemberStatus.ADMINISTRATOR
            can_post = is_creator or bool(getattr(member, "can_post_messages", False))
            if not is_admin or not can_post:
                return ChannelValidationResult(False)

            invite_url = None
            if not chat.username:
                existing_invite = getattr(chat, "invite_link", None)
                invite_url = getattr(existing_invite, "invite_link", None)
                if not invite_url:
                    invite_url = (
                        await self.bot.create_chat_invite_link(
                            channel_id,
                            name="smart-link-bot",
                            creates_join_request=False,
                        )
                    ).invite_link

            return ChannelValidationResult(
                True,
                title=chat.title or str(channel_id),
                username=chat.username,
                invite_url=invite_url,
            )
        except TelegramAPIError as exc:
            log.warning("Channel validation failed for %s: %s", channel_id, exc)
            return ChannelValidationResult(False)


class SubscriptionService:
    def __init__(self, bot: Bot, session: AsyncSession, owner_id: int):
        self.bot, self.channels, self.owner_id = (
            bot,
            ChannelRepository(session),
            owner_id,
        )

    async def check(self, user_id: int) -> bool | None:
        channel = await self.channels.get(self.owner_id)
        if not channel:
            return None
        try:
            member = await self.bot.get_chat_member(channel.channel_id, user_id)
            if member.status in {
                ChatMemberStatus.CREATOR,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.MEMBER,
            }:
                return True
            return member.status == ChatMemberStatus.RESTRICTED and getattr(
                member, "is_member", False
            )
        except TelegramAPIError as exc:
            log.warning(
                "Subscription check failed for channel %s: %s", channel.channel_id, exc
            )
            return None


async def send_link_content(
    bot: Bot,
    chat_id: int,
    link: object,
    buttons: RichButtons | None = None,
    storage: FileStorageService | None = None,
) -> None:
    kind, text, caption, file_id = (
        getattr(link, x) for x in ("content_type", "text", "caption", "file_id")
    )
    message_text = getattr(link, "message_text", None)
    if kind == "github_repository":
        await bot.send_rich_message(
            chat_id, rich_message(message_text or "", github_download_keyboard(link.id))
        )
        return
    if kind == "none":
        if buttons:
            await bot.send_rich_message(
                chat_id, rich_message(message_text or "", buttons)
            )
        else:
            await bot.send_rich_message(chat_id, rich_message(message_text or "", []))
        return
    if message_text:
        await bot.send_rich_message(chat_id, rich_message(message_text, []))
    local_path = getattr(link, "relative_path", None)
    if storage and local_path and not await storage.exists(local_path):
        raise FileNotFoundError(local_path)
    local_file = (
        FSInputFile(
            storage.resolve_path(local_path),
            filename=getattr(link, "original_filename", None),
        )
        if storage and local_path
        else None
    )
    if kind == "text":
        if buttons:
            await bot.send_rich_message(chat_id, rich_message(text or "", buttons))
        else:
            await bot.send_rich_message(chat_id, rich_message(text or "", []))
        return
    if kind == "photo":
        await bot.send_photo(chat_id, local_file or file_id or "", caption=footer_text(caption or "") or None)
    elif kind == "video":
        await bot.send_video(chat_id, local_file or file_id or "", caption=footer_text(caption or "") or None)
    elif kind == "document":
        await bot.send_document(chat_id, local_file or file_id or "", caption=footer_text(caption or "") or None)
    elif kind == "animation":
        await bot.send_animation(chat_id, local_file or file_id or "", caption=footer_text(caption or "") or None)
    if buttons:
        await bot.send_rich_message(chat_id, rich_message("", buttons))


async def send_stored_file(
    bot: Bot, chat_id: int, file: object, storage: FileStorageService
) -> None:
    local_path = file.relative_path
    if local_path and await storage.exists(local_path):
        source = FSInputFile(
            storage.resolve_path(local_path),
            filename=file.original_filename or file.file_name,
        )
    elif file.file_id:  # Legacy records that have not been imported yet.
        source = file.file_id
    else:
        raise FileNotFoundError(local_path or "missing fallback file")
    if file.file_type == "photo":
        await bot.send_photo(chat_id, source, caption=footer_text() or None)
    elif file.file_type == "video":
        await bot.send_video(chat_id, source, caption=footer_text() or None)
    elif file.file_type == "animation":
        await bot.send_animation(chat_id, source, caption=footer_text() or None)
    else:
        await bot.send_document(chat_id, source, caption=footer_text() or None)


class LinkService:
    def __init__(self, session: AsyncSession):
        self.links, self.visits = LinkRepository(session), VisitRepository(session)

    async def create(
        self,
        name: str,
        content: dict[str, str | None],
        created_by: int,
        fallback_files: list[dict[str, str | None]] | None = None,
    ) -> object:
        for _ in range(8):
            candidate = slug()
            if not await self.links.by_slug(candidate):
                link = await self.links.create(
                    slug=candidate,
                    name=name,
                    created_by=created_by,
                    is_active=True,
                    **content,
                )
                if fallback_files:
                    await FallbackFileRepository(self.links.session).replace(
                        link.id, fallback_files
                    )
                return link
        raise RuntimeError("Could not generate unique smart-link slug")

    async def record(self, link_id: int, user: User, subscribed: bool):
        return await self.visits.touch(link_id, user, subscribed)


class TrafficSourceService:
    def __init__(self, session: AsyncSession):
        self.sources = SourceRepository(session)

    async def create(self, smart_link_id: int, name: str):
        for _ in range(8):
            token = secrets.token_urlsafe(12).replace("-", "A").replace("_", "B")[:18]
            if not await self.sources.by_token(token):
                return await self.sources.create(smart_link_id, name, token)
        raise RuntimeError("Could not generate unique traffic-source token")


class AdminInviteService:
    def __init__(self, session: AsyncSession):
        self.repo = InviteRepository(session)

    async def create(self, creator: int) -> str:
        token = secrets.token_urlsafe(24)
        await self.repo.create(token, creator, datetime.now(UTC) + timedelta(hours=24))
        return token


def stats_text(
    name: str, data: dict[str, int], downloads_count: int | None = None
) -> str:
    initial = data["not_subscribed_initially"]
    conversion = data["subscribed_after_redirect"] / initial * 100 if initial else 0
    downloads = (
        f"\nСкачиваний ZIP: {downloads_count}" if downloads_count is not None else ""
    )
    return (
        f"<b>{name}</b>\n\nВсего переходов: {data['total_visits']}\nУникальных пользователей: {data['unique_users']}\n\n"
        f"Уже были подписаны: {data['already_subscribed']}\nНе были подписаны: {initial}\n"
        f"Подписались после перехода: {data['subscribed_after_redirect']}\n\nКонверсия в подписку: {conversion:.2f}%{downloads}"
    )
