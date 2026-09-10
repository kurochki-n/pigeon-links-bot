from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.database.base import Base


class SmartLink(Base):
    __tablename__ = "smart_links"
    __table_args__ = (
        Index("ix_smart_links_slug", "slug", unique=True),
        Index("ix_smart_links_active", "is_active"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    content_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # `message_text` is the administrator's message. The other fields describe material.
    message_text: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    # Legacy Telegram file ID is retained only to serve existing records during migration.
    file_id: Mapped[str | None] = mapped_column(String(255))
    relative_path: Mapped[str | None] = mapped_column(String(512))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(127))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    github_owner: Mapped[str | None] = mapped_column(String(100))
    github_repo: Mapped[str | None] = mapped_column(String(100))
    github_url: Mapped[str | None] = mapped_column(String(255))
    downloads_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    visits: Mapped[list["SmartLinkVisit"]] = relationship(
        back_populates="smart_link", cascade="all, delete-orphan"
    )
    visit_events: Mapped[list["SmartLinkVisitEvent"]] = relationship(
        back_populates="smart_link", cascade="all, delete-orphan"
    )
    fallback_files: Mapped[list["SmartLinkFallbackFile"]] = relationship(
        back_populates="smart_link",
        cascade="all, delete-orphan",
        order_by="SmartLinkFallbackFile.position",
    )
    sources: Mapped[list["SmartLinkSource"]] = relationship(
        back_populates="smart_link", cascade="all, delete-orphan"
    )
    content_items: Mapped[list["SmartLinkContentItem"]] = relationship(
        back_populates="smart_link",
        cascade="all, delete-orphan",
        order_by="SmartLinkContentItem.position",
    )
    resources: Mapped[list["SmartLinkResource"]] = relationship(
        back_populates="smart_link",
        cascade="all, delete-orphan",
        order_by="SmartLinkResource.position",
    )


class SmartLinkContentItem(Base):
    __tablename__ = "smart_link_content_items"
    __table_args__ = (Index("ix_content_items_link", "smart_link_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    smart_link_id: Mapped[int] = mapped_column(
        ForeignKey("smart_links.id"), nullable=False
    )
    content_type: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[str | None] = mapped_column(String(255))
    relative_path: Mapped[str | None] = mapped_column(String(512))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(127))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    smart_link: Mapped[SmartLink] = relationship(back_populates="content_items")


class SmartLinkResource(Base):
    __tablename__ = "smart_link_resources"
    __table_args__ = (Index("ix_resources_link", "smart_link_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    smart_link_id: Mapped[int] = mapped_column(
        ForeignKey("smart_links.id"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    github_owner: Mapped[str | None] = mapped_column(String(100))
    github_repo: Mapped[str | None] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    smart_link: Mapped[SmartLink] = relationship(back_populates="resources")


class SmartLinkSource(Base):
    __tablename__ = "smart_link_sources"
    __table_args__ = (
        Index("ix_sources_token", "token", unique=True),
        Index("ix_sources_link", "smart_link_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    smart_link_id: Mapped[int] = mapped_column(
        ForeignKey("smart_links.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    smart_link: Mapped[SmartLink] = relationship(back_populates="sources")
    visits: Mapped[list["SmartLinkSourceVisit"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    visit_events: Mapped[list["SmartLinkVisitEvent"]] = relationship(
        back_populates="source"
    )


class SmartLinkFallbackFile(Base):
    __tablename__ = "smart_link_fallback_files"
    __table_args__ = (Index("ix_fallback_files_link", "smart_link_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    smart_link_id: Mapped[int] = mapped_column(
        ForeignKey("smart_links.id"), nullable=False
    )
    # `file_id` supports legacy rows only; new fallback records use `relative_path`.
    file_id: Mapped[str | None] = mapped_column(String(255))
    relative_path: Mapped[str | None] = mapped_column(String(512))
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(127))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    smart_link: Mapped[SmartLink] = relationship(back_populates="fallback_files")


class TelegramUser(Base):
    __tablename__ = "telegram_users"
    __table_args__ = (Index("ix_telegram_users_telegram_id", "telegram_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    bio: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    visit_events: Mapped[list["SmartLinkVisitEvent"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class SmartLinkVisit(Base):
    __tablename__ = "smart_link_visits"
    __table_args__ = (
        UniqueConstraint("smart_link_id", "telegram_user_id", name="uq_link_user"),
        Index("ix_visits_link", "smart_link_id"),
        Index("ix_visits_user", "telegram_user_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    smart_link_id: Mapped[int] = mapped_column(
        ForeignKey("smart_links.id"), nullable=False
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    was_subscribed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    subscribed_after: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    first_visit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_visit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conversion_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("smart_link_sources.id")
    )
    visits_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    smart_link: Mapped[SmartLink] = relationship(back_populates="visits")


class SmartLinkVisitEvent(Base):
    __tablename__ = "smart_link_visit_events"
    __table_args__ = (
        Index("ix_visit_events_link", "smart_link_id"),
        Index("ix_visit_events_user", "user_id"),
        Index("ix_visit_events_source", "source_id"),
        Index("ix_visit_events_visited_at", "visited_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.id"), nullable=False
    )
    smart_link_id: Mapped[int] = mapped_column(
        ForeignKey("smart_links.id"), nullable=False
    )
    source_id: Mapped[int | None] = mapped_column(ForeignKey("smart_link_sources.id"))
    visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    was_subscribed: Mapped[bool | None] = mapped_column(Boolean)
    subscribed_after: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[TelegramUser] = relationship(back_populates="visit_events")
    smart_link: Mapped[SmartLink] = relationship(back_populates="visit_events")
    source: Mapped[SmartLinkSource | None] = relationship(back_populates="visit_events")


class SmartLinkSourceVisit(Base):
    __tablename__ = "smart_link_source_visits"
    __table_args__ = (
        UniqueConstraint("source_id", "telegram_user_id", name="uq_source_user"),
        Index("ix_source_visits_source", "source_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("smart_link_sources.id"), nullable=False
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    was_subscribed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    subscribed_after: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    first_visit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_visit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    visits_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source: Mapped[SmartLinkSource] = relationship(back_populates="visits")


class DeliveryBotSettings(Base):
    __tablename__ = "delivery_bot_settings"

    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ChannelConnectToken(Base):
    __tablename__ = "channel_connect_tokens"
    __table_args__ = (
        Index("ix_channel_connect_token_hash", "token_hash", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserChannelSettings(Base):
    __tablename__ = "user_channel_settings"

    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    invite_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
