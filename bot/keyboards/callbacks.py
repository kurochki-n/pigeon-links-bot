from aiogram.filters.callback_data import CallbackData


class ChannelCallback(CallbackData, prefix="chn"):
    action: str


class LinkCallback(CallbackData, prefix="lnk"):
    action: str
    link_id: int = 0
    page: int = 0


class StatsCallback(CallbackData, prefix="sts"):
    action: str
    link_id: int = 0
    page: int = 0


class MaterialCallback(CallbackData, prefix="material"):
    action: str


class SourceCallback(CallbackData, prefix="source"):
    action: str
    link_id: int = 0
    source_id: int = 0


class SubscriptionCallback(CallbackData, prefix="sub"):
    link_id: int
    source_id: int = 0


class ResourceCallback(CallbackData, prefix="resource"):
    resource_id: int


class PostCallback(CallbackData, prefix="pst"):
    action: str
