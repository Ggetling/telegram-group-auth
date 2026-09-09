"""aiogram 3 adapter. Import this only if aiogram is installed.

Whole integration, for a bot that already has a ``Dispatcher``::

    from group_auth import AuthConfig, MembershipChecker, MemoryStore
    from group_auth.aiogram3 import install, lifecycle_router

    checker = MembershipChecker(AuthConfig.from_env(), store=MemoryStore())
    await checker.load()
    dp.include_router(lifecycle_router(checker))   # first
    install(dp, checker)
    dp.include_router(your_router)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
"""

from __future__ import annotations

from .filters import AdminFilter, NotAdminFilter
from .lifecycle import lifecycle_router
from .middleware import (
    GATED_OBSERVERS,
    UNGATED_OBSERVERS,
    AccessMiddleware,
    default_on_denied,
    install,
)

__all__ = [
    "GATED_OBSERVERS",
    "UNGATED_OBSERVERS",
    "AccessMiddleware",
    "AdminFilter",
    "NotAdminFilter",
    "default_on_denied",
    "install",
    "lifecycle_router",
]
