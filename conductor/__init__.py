"""persona_conductor 子包：状态持久化与世界书注入逻辑。

注意：包内只放纯逻辑，Star 类与 @register / @filter 必须留在入口 main.py，
否则 AstrBot 会报「未通过 Star 注册」。
"""

from .inject import (  # noqa: F401
    HINT_TEXT,
    MODE_DESC,
    MODE_LABELS,
    MODES,
    build_worldbook_block,
    join_body,
    load_worldbook,
    normalize_mode,
    parse_uid_set,
    select_entries,
    source_chars,
    worldbook_stats,
)
from .store import StateStore  # noqa: F401

__all__ = [
    "HINT_TEXT",
    "MODE_DESC",
    "MODE_LABELS",
    "MODES",
    "StateStore",
    "build_worldbook_block",
    "join_body",
    "load_worldbook",
    "normalize_mode",
    "parse_uid_set",
    "select_entries",
    "source_chars",
    "worldbook_stats",
]
