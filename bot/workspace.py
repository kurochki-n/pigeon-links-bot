from contextvars import ContextVar

workspace_owner_id: ContextVar[int | None] = ContextVar(
    "workspace_owner_id", default=None
)
