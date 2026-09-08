from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.database.models import (
    AdminInvite,
    SmartLink,
    SmartLinkFallbackFile,
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


def is_expired(value: datetime) -> bool:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)) <= now()


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
        if scoped and (owner_id := workspace_owner_id.get()) is not None:
            stmt = stmt.where(SmartLink.created_by == owner_id)
        if active_only:
            stmt = stmt.where(SmartLink.is_active.is_(True))
        return await self.session.scalar(stmt)

    async def by_slug(self, slug: str) -> SmartLink | None:
        return await self.session.scalar(
            select(SmartLink).where(SmartLink.slug == slug)
        )

    async def page(self, page: int, size: int = 8) -> tuple[list[SmartLink], int]:
        condition = SmartLink.is_active.is_(True)
        if (owner_id := workspace_owner_id.get()) is not None:
            condition = condition & (SmartLink.created_by == owner_id)
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
                    .where(SmartLink.created_by == workspace_owner_id.get())
                    .order_by(SmartLink.created_at.desc())
                )
            ).all()
        )

    async def downloads_total(self, link_id: int | None = None) -> int:
        stmt = select(func.coalesce(func.sum(SmartLink.downloads_count), 0))
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
        if scoped and (owner_id := workspace_owner_id.get()) is not None:
            stmt = stmt.join(SmartLink).where(SmartLink.created_by == owner_id)
        if active_only:
            stmt = stmt.where(SmartLinkSource.is_active.is_(True))
        return await self.session.scalar(stmt)

    async def by_token(self, token: str) -> SmartLinkSource | None:
        return await self.session.scalar(
            select(SmartLinkSource).where(SmartLinkSource.token == token)
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
        self.session.add(item)
        await self.session.flush()
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
        self.session.add(visit)
        await self.session.flush()
        return visit

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
        self.session.add(visit)
        await self.session.flush()
        return visit

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
        stmt = select(SmartLinkVisit)
        if link_id:
            stmt = stmt.where(SmartLinkVisit.smart_link_id == link_id)
        visits = (await self.session.scalars(stmt)).all()
        initial_no = sum(not x.was_subscribed for x in visits)
        return {
            "total_visits": sum(x.visits_count for x in visits),
            "unique_users": len(visits),
            "already_subscribed": sum(x.was_subscribed for x in visits),
            "not_subscribed_initially": initial_no,
            "subscribed_after_redirect": sum(x.subscribed_after for x in visits),
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
                    .where(SmartLinkVisit.smart_link_id == link_id)
                    .order_by(SmartLinkVisit.last_visit_at.desc())
                )
            ).all()
        )

    async def event_rows(self, link_id: int) -> list[SmartLinkVisitEvent]:
        return list(
            (
                await self.session.scalars(
                    select(SmartLinkVisitEvent)
                    .where(SmartLinkVisitEvent.smart_link_id == link_id)
                    .options(
                        selectinload(SmartLinkVisitEvent.user),
                        selectinload(SmartLinkVisitEvent.source),
                    )
                    .order_by(SmartLinkVisitEvent.visited_at.desc())
                )
            ).all()
        )


class ChannelRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, owner_id: int | None = None) -> UserChannelSettings | None:
        return await self.session.get(
            UserChannelSettings, owner_id or workspace_owner_id.get()
        )

    async def save(self, **data: object) -> UserChannelSettings:
        owner_id = workspace_owner_id.get()
        if owner_id is None:
            raise RuntimeError("Workspace owner is required")
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

    async def delete(self) -> None:
        item = await self.get()
        if item:
            await self.session.delete(item)


class InviteRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, token: str, creator: int, expires_at: datetime
    ) -> AdminInvite:
        obj = AdminInvite(
            token=token, created_by=creator, created_at=now(), expires_at=expires_at
        )
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def consume(self, token: str, user_id: int) -> bool:
        item = await self.session.scalar(
            select(AdminInvite).where(AdminInvite.token == token)
        )
        if not item or item.used_at or is_expired(item.expires_at):
            return False
        item.used_at = now()
        item.used_by = user_id
        return True
