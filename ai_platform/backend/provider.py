"""Request-scoped model selection shared by graph and context accounting."""

from contextvars import ContextVar

local_profile: ContextVar[str | None] = ContextVar("local_profile", default=None)
