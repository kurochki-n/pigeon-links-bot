from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.database.models import (
    ChannelConnectToken,
    DeliveryBotSettings,
    SmartLink,
    SmartLinkContentItem,
    SmartLinkFallbackFile,
    SmartLinkResource,
    SmartLinkSource,
    SmartLinkSourceVisit,
    SmartLinkVisit,
    SmartLinkVisitEvent,
    TelegramUser,
    UserChannelSettings,
)
from bot.workspace import workspace_owner_id


def now() -> datetime:
    return datetime.now(UTC)


def current_owner_id() -> int:
    owner_id = workspace_owner_id.get()
    if owner_id is None:
        raise RuntimeError("User workspace is unavailable for this update")
    return owner_id


class LinkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **data: object) -> SmartLink:
        item = SmartLink(**data, created_at=now(), updated_at=now())
        self.session.add(item)
        await self.session.flush()
        return item

    async def get(
        self, link_id: int, active_only: bool = False, scoped: bool = True
    ) -> SmartLink | None:
        stmt = select(SmartLink).where(SmartLink.id == link_id)
        if scoped:
            stmt = stmt.where(SmartLink.created_by == current_owner_id())
        if active_only:
            stmt = stmt.where(SmartLink.is_active.is_(True))
        return await self.session.scalar(stmt)

    async def by_slug(self, slug: str) -> SmartLink | None:
        return await self.session.scalar(
            select(SmartLink).where(SmartLink.slug == slug)
        )

    async def page(self, page: int, size: int = 8) -> tuple[list[SmartLink], int]:
        condition = SmartLink.is_active.is_(True) & (
            SmartLink.created_by == current_owner_id()
        )
        total = (
            await self.session.scalar(
                select(func.count()).select_from(SmartLink).where(condition)
            )
            or 0
        )
        rows = (
            await self.session.scalars(
                select(SmartLink)
                .where(condition)
                .order_by(SmartLink.created_at.desc())
                .offset(page * size)
                .limit(size)
            )
        ).all()
        return list(rows), total

    async def all(self) -> list[SmartLink]:
        return list(
            (
                await self.session.scalars(
                    select(SmartLink)
                    .where(SmartLink.created_by == current_owner_id())
                    .order_by(SmartLink.created_at.desc())
                )
            ).all()
        )

    async def downloads_total(self, link_id: int | None = None) -> int:
        stmt = select(func.coalesce(func.sum(SmartLink.downloads_count), 0)).where(
            SmartLink.created_by == current_owner_id()
        )
        if link_id is not None:
            stmt = stmt.where(SmartLink.id == link_id)
        return int(await self.session.scalar(stmt) or 0)

    async def increment_downloads(self, link: SmartLink) -> None:
        link.downloads_count += 1
        link.updated_at = now()

    async def deactivate(self, link: SmartLink) -> None:
        link.is_active = False
        link.updated_at = now()


class SourceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, smart_link_id: int, name: str, token: str
    ) -> SmartLinkSource:
        source = SmartLinkSource(
            smart_link_id=smart_link_id,
            name=name,
            token=token,
            is_active=True,
            created_at=now(),
        )
        self.session.add(source)
        await self.session.flush()
        return source

    async def get(
        self, source_id: int, active_only: bool = False, scoped: bool = True
    ) -> SmartLinkSource | None:
        stmt = select(SmartLinkSource).where(SmartLinkSource.id == source_id)
        if scoped:
            stmt = stmt.join(SmartLink).where(
                SmartLink.created_by == current_owner_id()
            )
        if active_only:
            stmt = stmt.where(SmartLinkSource.is_active.is_(True))
        return await self.session.scalar(stmt)

    async def by_token(self, token: str) -> SmartLinkSource | None:
        return await self.session.scalar(
            select(SmartLinkSource).where(SmartLinkSource.token == token)
        )

    async def count_for_link(self, smart_link_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(SmartLinkSource)
                .where(SmartLinkSource.smart_link_id == smart_link_id)
            )
            or 0
        )

    async def list_for_link(self, smart_link_id: int) -> list[SmartLinkSource]:
        return list(
            (
                await self.session.scalars(
                    select(SmartLinkSource)
                    .where(SmartLinkSource.smart_link_id == smart_link_id)
                    .order_by(SmartLinkSource.created_at)
                )
            ).all()
        )


class ContentItemRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def replace(self, smart_link_id: int, items: list[dict[str, object]]) -> None:
        await self.session.execute(
            delete(SmartLinkContentItem).where(
                SmartLinkContentItem.smart_link_id == smart_link_id
            )
        )
        for position, item in enumerate(items):
            self.session.add(
                SmartLinkContentItem(
                    smart_link_id=smart_link_id,
                    content_type=str(item["content_type"]),
                    text=item.get("text"),
                    caption=item.get("caption"),
                    file_id=item.get("file_id"),
                    relative_path=item.get("relative_path"),
                    original_filename=item.get("original_filename"),
                    mime_type=item.get("mime_type"),
                    file_size=item.get("file_size"),
                    position=position,
                    created_at=now(),
                )
            )
        await self.session.flush()

    async def list_for_link(self, smart_link_id: int) -> list[SmartLinkContentItem]:
        return list(
            (
                await self.session.scalars(
                    select(SmartLinkContentItem)
                    .where(SmartLinkContentItem.smart_link_id == smart_link_id)
                    .order_by(SmartLinkContentItem.position)
                )
            ).all()
        )


class ResourceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def replace(self, smart_link_id: int, items: list[dict[str, object]]) -> None:
        await self.session.execute(
            delete(SmartLinkResource).where(
                SmartLinkResource.smart_link_id == smart_link_id
            )
        )
        for position, item in enumerate(items):
            self.session.add(
                SmartLinkResource(
                    smart_link_id=smart_link_id,
                    resource_type=str(item["resource_type"]),
                    title=str(item["title"]),
                    url=str(item["url"]),
                    github_owner=item.get("github_owner"),
                    github_repo=item.get("github_repo"),
                    position=position,
                    created_at=now(),
                )
            )
        await self.session.flush()

    async def list_for_link(self, smart_link_id: int) -> list[SmartLinkResource]:
        return list(
            (
                await self.session.scalars(
                    select(SmartLinkResource)
                    .where(SmartLinkResource.smart_link_id == smart_link_id)
                    .order_by(SmartLinkResource.position)
                )
            ).all()
        )

    async def get(self, resource_id: int) -> SmartLinkResource | None:
        return await self.session.scalar(
            select(SmartLinkResource)
            .where(SmartLinkResource.id == resource_id)
            .options(selectinload(SmartLinkResource.smart_link))
        )


class FallbackFileRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, smart_link_id: int, **data: object) -> SmartLinkFallbackFile:
        position = (
            await self.session.scalar(
                select(
                    func.coalesce(func.max(SmartLinkFallbackFile.position), -1)
                ).where(SmartLinkFallbackFile.smart_link_id == smart_link_id)
            )
            + 1
        )
        item = SmartLinkFallbackFile(
            smart_link_id=smart_link_id,
            position=position,
            created_at=now(),
            **data,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_for_link(self, smart_link_id: int) -> list[SmartLinkFallbackFile]:
        return list(
            (
                await self.session.scalars(
                    select(SmartLinkFallbackFile)
                    .where(SmartLinkFallbackFile.smart_link_id == smart_link_id)
                    .order_by(SmartLinkFallbackFile.position)
                )
            ).all()
        )

    async def replace(
        self, smart_link_id: int, files: list[dict[str, str | None]]
    ) -> None:
        await self.session.execute(
            delete(SmartLinkFallbackFile).where(
                SmartLinkFallbackFile.smart_link_id == smart_link_id
            )
        )
        for position, file in enumerate(files):
            self.session.add(
                SmartLinkFallbackFile(
                    smart_link_id=smart_link_id,
                    file_id=file.get("file_id"),
                    relative_path=file.get("relative_path"),
                    file_type=file["file_type"] or "document",
                    file_name=file.get("file_name"),
                    original_filename=file.get("original_filename"),
                    mime_type=file.get("mime_type"),
                    file_size=int(file["file_size"]) if file.get("file_size") else None,
                    position=position,
                    created_at=now(),
                )
            )
        await self.session.flush()


class TelegramUserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(
        self, user: object, bio: str | None, updated_at: datetime
    ) -> TelegramUser:
        telegram_id = user.id
        item = await self.session.scalar(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        fields = {
            "username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
        }
        if bio is not None:
            fields["bio"] = bio
        if item:
            for key, value in fields.items():
                setattr(item, key, value)
            item.updated_at = updated_at
            return item
        item = TelegramUser(
            telegram_id=telegram_id,
            created_at=updated_at,
            updated_at=updated_at,
            **fields,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(item)
                await self.session.flush()
            return item
        except IntegrityError:
            item = await self.session.scalar(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
            )
            if item is None:
                raise
            for key, value in fields.items():
                setattr(item, key, value)
            item.updated_at = updated_at
            return item

    async def by_telegram_ids(self, telegram_ids: set[int]) -> dict[int, TelegramUser]:
        if not telegram_ids:
            return {}
        users = await self.session.scalars(
            select(TelegramUser).where(TelegramUser.telegram_id.in_(telegram_ids))
        )
        return {user.telegram_id: user for user in users}


class VisitRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = TelegramUserRepository(session)

    async def touch(
        self,
        link_id: int,
        user: object,
        subscribed: bool | None,
        bio: str | None = None,
        source_id: int | None = None,
    ) -> SmartLinkVisit | None:
        visited_at = now()
        profile = await self.users.upsert(user, bio, visited_at)
        self.session.add(
            SmartLinkVisitEvent(
                user_id=profile.id,
                smart_link_id=link_id,
                source_id=source_id,
                visited_at=visited_at,
                was_subscribed=subscribed,
                subscribed_after=False,
            )
        )
        if subscribed is None:
            await self.session.flush()
            return None

        user_id = profile.telegram_id
        visit = await self.session.scalar(
            select(SmartLinkVisit).where(
                SmartLinkVisit.smart_link_id == link_id,
                SmartLinkVisit.telegram_user_id == user_id,
            )
        )
        if visit:
            visit.username = profile.username
            visit.first_name = profile.first_name
            visit.last_name = profile.last_name
            visit.visits_count += 1
            visit.last_visit_at = visited_at
            await self.session.flush()
            return visit
        visit = SmartLinkVisit(
            smart_link_id=link_id,
            telegram_user_id=user_id,
            username=profile.username,
            first_name=profile.first_name,
            last_name=profile.last_name,
            was_subscribed=subscribed,
            subscribed_after=False,
            first_visit_at=visited_at,
            last_visit_at=visited_at,
            visits_count=1,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(visit)
                await self.session.flush()
            return visit
        except IntegrityError:
            existing = await self.session.scalar(
                select(SmartLinkVisit).where(
                    SmartLinkVisit.smart_link_id == link_id,
                    SmartLinkVisit.telegram_user_id == user_id,
                )
            )
            if existing is None:
                raise
            existing.visits_count += 1
            existing.last_visit_at = visited_at
            return existing

    async def touch_source(
        self, source_id: int, user_id: int, subscribed: bool
    ) -> SmartLinkSourceVisit:
        visit = await self.session.scalar(
            select(SmartLinkSourceVisit).where(
                SmartLinkSourceVisit.source_id == source_id,
                SmartLinkSourceVisit.telegram_user_id == user_id,
            )
        )
        if visit:
            visit.visits_count += 1
            visit.last_visit_at = now()
            return visit
        visit = SmartLinkSourceVisit(
            source_id=source_id,
            telegram_user_id=user_id,
            was_subscribed=subscribed,
            subscribed_after=False,
            first_visit_at=now(),
            last_visit_at=now(),
            visits_count=1,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(visit)
                await self.session.flush()
            return visit
        except IntegrityError:
            existing = await self.session.scalar(
                select(SmartLinkSourceVisit).where(
                    SmartLinkSourceVisit.source_id == source_id,
                    SmartLinkSourceVisit.telegram_user_id == user_id,
                )
            )
            if existing is None:
                raise
            existing.visits_count += 1
            existing.last_visit_at = now()
            return existing

    async def mark_subscribed(
        self, link_id: int, user_id: int, source_id: int | None = None
    ) -> bool:
        visit = await self.session.scalar(
            select(SmartLinkVisit).where(
                SmartLinkVisit.smart_link_id == link_id,
                SmartLinkVisit.telegram_user_id == user_id,
            )
        )
        if not visit or visit.was_subscribed or visit.subscribed_after:
            return False
        visit.subscribed_after = True
        visit.subscribed_at = now()
        visit.conversion_source_id = source_id
        if source_id is not None:
            source_visit = await self.session.scalar(
                select(SmartLinkSourceVisit).where(
                    SmartLinkSourceVisit.source_id == source_id,
                    SmartLinkSourceVisit.telegram_user_id == user_id,
                )
            )
            if source_visit and not source_visit.was_subscribed:
                source_visit.subscribed_after = True
                source_visit.subscribed_at = now()
        await self.mark_latest_event_subscribed(link_id, user_id, source_id)
        return True

    async def mark_latest_event_subscribed(
        self, link_id: int, telegram_id: int, source_id: int | None
    ) -> None:
        user = await self.session.scalar(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        if not user:
            return
        stmt = (
            select(SmartLinkVisitEvent)
            .where(
                SmartLinkVisitEvent.smart_link_id == link_id,
                SmartLinkVisitEvent.user_id == user.id,
                (
                    SmartLinkVisitEvent.source_id == source_id
                    if source_id is not None
                    else SmartLinkVisitEvent.source_id.is_(None)
                ),
            )
            .order_by(
                SmartLinkVisitEvent.visited_at.desc(), SmartLinkVisitEvent.id.desc()
            )
        )
        event = await self.session.scalar(stmt)
        if event and not event.was_subscribed and not event.subscribed_after:
            event.subscribed_after = True
            event.subscribed_at = now()

    async def summary(self, link_id: int | None = None) -> dict[str, int]:
        stmt = (
            select(SmartLinkVisit)
            .join(SmartLink)
            .where(SmartLink.created_by == current_owner_id())
        )
        if link_id is not None:
            stmt = stmt.where(SmartLinkVisit.smart_link_id == link_id)
        visits = list((await self.session.scalars(stmt)).all())

        if link_id is not None:
            initial_no = sum(not visit.was_subscribed for visit in visits)
            return {
                "total_visits": sum(visit.visits_count for visit in visits),
                "unique_users": len(visits),
                "already_subscribed": sum(visit.was_subscribed for visit in visits),
                "not_subscribed_initially": initial_no,
                "subscribed_after_redirect": sum(
                    visit.subscribed_after for visit in visits
                ),
            }

        by_user: dict[int, list[SmartLinkVisit]] = {}
        for visit in visits:
            by_user.setdefault(visit.telegram_user_id, []).append(visit)
        first_visits = [
            min(user_visits, key=lambda visit: visit.first_visit_at)
            for user_visits in by_user.values()
        ]
        initially_not_subscribed = [
            visit for visit in first_visits if not visit.was_subscribed
        ]
        converted_users = sum(
            any(visit.subscribed_after for visit in by_user[first.telegram_user_id])
            for first in initially_not_subscribed
        )
        return {
            "total_visits": sum(visit.visits_count for visit in visits),
            "unique_users": len(by_user),
            "already_subscribed": sum(visit.was_subscribed for visit in first_visits),
            "not_subscribed_initially": len(initially_not_subscribed),
            "subscribed_after_redirect": converted_users,
        }

    async def source_summary(self, source_id: int) -> dict[str, int]:
        visits = list(
            (
                await self.session.scalars(
                    select(SmartLinkSourceVisit).where(
                        SmartLinkSourceVisit.source_id == source_id
                    )
                )
            ).all()
        )
        initial_no = sum(not x.was_subscribed for x in visits)
        return {
            "total_visits": sum(x.visits_count for x in visits),
            "unique_users": len(visits),
            "already_subscribed": sum(x.was_subscribed for x in visits),
            "not_subscribed_initially": initial_no,
            "subscribed_after_redirect": sum(x.subscribed_after for x in visits),
        }

    async def rows(self, link_id: int) -> list[SmartLinkVisit]:
        return list(
            (
                await self.session.scalars(
                    select(SmartLinkVisit)
                    .join(SmartLink)
                    .where(
                        SmartLinkVisit.smart_link_id == link_id,
                        SmartLink.created_by == current_owner_id(),
                    )
                    .order_by(SmartLinkVisit.last_visit_at.desc())
                )
            ).all()
        )

    async def event_rows(self, link_id: int) -> list[SmartLinkVisitEvent]:
        return list(
            (
                await self.session.scalars(
                    select(SmartLinkVisitEvent)
                    .join(SmartLink)
                    .where(
                        SmartLinkVisitEvent.smart_link_id == link_id,
                        SmartLink.created_by == current_owner_id(),
                    )
                    .options(
                        selectinload(SmartLinkVisitEvent.user),
                        selectinload(SmartLinkVisitEvent.source),
                    )
                    .order_by(SmartLinkVisitEvent.visited_at.desc())
                )
            ).all()
        )


class DeliveryBotRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, owner_id: int | None = None) -> DeliveryBotSettings | None:
        resolved_owner_id = owner_id if owner_id is not None else current_owner_id()
        return await self.session.get(DeliveryBotSettings, resolved_owner_id)

    async def by_bot_id(self, bot_id: int) -> DeliveryBotSettings | None:
        return await self.session.scalar(
            select(DeliveryBotSettings).where(DeliveryBotSettings.bot_id == bot_id)
        )

    async def all(self) -> list[DeliveryBotSettings]:
        return list((await self.session.scalars(select(DeliveryBotSettings))).all())

    async def save(
        self, bot_id: int, username: str, encrypted_token: str
    ) -> DeliveryBotSettings:
        owner_id = current_owner_id()
        item = await self.get(owner_id)
        if item is None:
            item = DeliveryBotSettings(
                owner_id=owner_id,
                bot_id=bot_id,
                username=username,
                encrypted_token=encrypted_token,
                created_at=now(),
                updated_at=now(),
            )
            self.session.add(item)
        else:
            item.bot_id = bot_id
            item.username = username
            item.encrypted_token = encrypted_token
            item.updated_at = now()
        await self.session.flush()
        return item

    async def delete(self) -> None:
        item = await self.get()
        if item:
            await self.session.delete(item)


class ChannelConnectTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, token_hash: str, owner_id: int, expires_at: datetime
    ) -> ChannelConnectToken:
        await self.session.execute(
            delete(ChannelConnectToken).where(
                (ChannelConnectToken.owner_id == owner_id)
                | (ChannelConnectToken.expires_at <= now())
            )
        )
        item = ChannelConnectToken(
            token_hash=token_hash,
            owner_id=owner_id,
            expires_at=expires_at,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def get_valid(self, token_hash: str) -> ChannelConnectToken | None:
        item = await self.session.scalar(
            select(ChannelConnectToken).where(
                ChannelConnectToken.token_hash == token_hash,
                ChannelConnectToken.used_at.is_(None),
            )
        )
        if item is None:
            return None
        expires_at = (
            item.expires_at
            if item.expires_at.tzinfo
            else item.expires_at.replace(tzinfo=UTC)
        )
        return item if expires_at > now() else None

    async def consume(self, item: ChannelConnectToken) -> None:
        item.used_at = now()


class ChannelRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def by_channel_id(self, channel_id: int) -> UserChannelSettings | None:
        return await self.session.scalar(
            select(UserChannelSettings).where(
                UserChannelSettings.channel_id == channel_id
            )
        )

    async def get(self, owner_id: int | None = None) -> UserChannelSettings | None:
        resolved_owner_id = owner_id if owner_id is not None else current_owner_id()
        return await self.session.get(UserChannelSettings, resolved_owner_id)

    async def save_for_owner(
        self, owner_id: int, **data: object
    ) -> UserChannelSettings:
        if owner_id <= 0:
            raise ValueError("owner_id must be positive")
        item = await self.get(owner_id)
        if item is None:
            item = UserChannelSettings(
                owner_id=owner_id, **data, created_at=now(), updated_at=now()
            )
            self.session.add(item)
        else:
            for key, value in data.items():
                setattr(item, key, value)
            item.updated_at = now()
        await self.session.flush()
        return item

    async def save(self, **data: object) -> UserChannelSettings:
        return await self.save_for_owner(current_owner_id(), **data)

    async def delete(self) -> None:
        item = await self.get()
        if item:
            await self.session.delete(item)
