"""世界书条目筛选与注入块组装。

纯逻辑模块：不导入 AstrBot 运行时，方便本地单测直接调用。

三种模式：
  normal —— 完全不注入（只用 AstrBot 现有 persona）
  tavern —— 注入完整世界书
  blend  —— 注入精简世界书（剔除与 persona 重复的角色档案，默认 uid=015 的人物深度档案）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

MODES: tuple[str, ...] = ("normal", "tavern", "blend")

MODE_LABELS: dict[str, str] = {
    "normal": "正常对话",
    "tavern": "酒馆模式",
    "blend": "融合模式",
}

MODE_DESC: dict[str, str] = {
    "normal": "只用 AstrBot 现有 persona，不注入任何世界书（默认）",
    "tavern": "注入完整世界书，由角色卡世界观驱动",
    "blend": "persona + 精简世界书（剔除重复的角色档案）",
}

HINT_TEXT = (
    "以上是本轮场景的世界设定，据此理解环境与人物关系；"
    "不要复述这些设定，像平常一样说话。"
)

TRUNCATE_MARK = "…（世界书已按 max_worldbook_chars 截断）"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def normalize_mode(value: Any, default: str = "normal") -> str:
    """把用户输入/持久化值归一成 normal|tavern|blend。"""
    if isinstance(value, str):
        v = value.strip().lower()
        alias = {
            "normal": "normal",
            "norm": "normal",
            "正常": "normal",
            "正常对话": "normal",
            "普通": "normal",
            "off": "normal",
            "none": "normal",
            "tavern": "tavern",
            "酒馆": "tavern",
            "酒馆模式": "tavern",
            "st": "tavern",
            "角色卡": "tavern",
            "blend": "blend",
            "融合": "blend",
            "融合模式": "blend",
            "混合": "blend",
            "mix": "blend",
        }
        if v in alias:
            return alias[v]
    return default


def _norm_uid(uid: Any) -> str:
    """uid 归一：数字补零到 3 位，便于 "15" 与 "015" 等价。"""
    s = str(uid or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return s.zfill(3)
    return s.upper()


def parse_uid_set(raw: Any) -> set[str]:
    """把 "015, 016" / ["015"] / None 解析成 uid 集合。"""
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        items: Iterable[Any] = raw
    else:
        items = str(raw).replace("，", ",").replace(" ", ",").split(",")
    out: set[str] = set()
    for item in items:
        u = _norm_uid(item)
        if u:
            out.add(u)
    return out


# --------------------------------------------------------------------------- #
# 世界书读取
# --------------------------------------------------------------------------- #
def load_worldbook(path: str | Path) -> list[dict]:
    """读取世界书 JSON，返回条目列表（每条至少含 uid/content）。"""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = data.get("entries") or data.get("worldbook") or []
    else:
        entries = []
    out: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        out.append(
            {
                "uid": _norm_uid(e.get("uid")),
                "title": str(e.get("title") or e.get("comment") or "").strip(),
                "keys": list(e.get("keys") or []),
                "constant": bool(e.get("constant", True)),
                "enabled": bool(e.get("enabled", True)),
                "order": int(e.get("order", e.get("insertion_order", 100)) or 100),
                "source_chars": int(e.get("source_chars") or 0),
                "content": str(e.get("content") or "").strip(),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 选条
# --------------------------------------------------------------------------- #
def select_entries(
    entries: Iterable[dict],
    mode: str,
    exclude_uids: Any = None,
) -> list[dict]:
    """按模式挑出要注入的条目。normal 恒为空。"""
    mode = normalize_mode(mode)
    if mode == "normal":
        return []
    exclude = parse_uid_set(exclude_uids)
    picked: list[dict] = []
    for e in entries:
        if not e.get("content"):
            continue
        if not e.get("enabled", True):
            continue
        if _norm_uid(e.get("uid")) in exclude:
            continue
        picked.append(e)
    picked.sort(key=lambda e: (e.get("order", 100), _norm_uid(e.get("uid"))))
    return picked


def join_body(entries: Iterable[dict]) -> str:
    parts = [str(e.get("content") or "").strip() for e in entries]
    return "\n\n".join(p for p in parts if p)


def source_chars(entries: Iterable[dict]) -> int:
    """素材原始字数（含被剥掉的 YAML 头），用于对账。"""
    return sum(int(e.get("source_chars") or 0) for e in entries)


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def build_worldbook_block(
    entries: Iterable[dict],
    mode: str,
    max_chars: int = 24000,
    include_hint: bool = True,
) -> str:
    """把条目拼成注入块。

    normal 或没有条目 → 返回空串（调用方据此跳过注入）。
    """
    mode = normalize_mode(mode)
    if mode == "normal":
        return ""

    body = join_body(entries)
    if not body:
        return ""

    truncated = False
    limit = int(max_chars or 0)
    if limit > 0 and len(body) > limit:
        body = body[:limit].rstrip()
        truncated = True

    lines = [f'<worldbook mode="{mode}">', body]
    if truncated:
        lines.append(TRUNCATE_MARK)
    lines.append("</worldbook>")
    block = "\n".join(lines)

    if include_hint:
        block = f"{block}\n\n{HINT_TEXT}"
    return block


def worldbook_stats(
    entries: Iterable[dict],
    mode: str,
    max_chars: int = 24000,
    include_hint: bool = True,
) -> dict:
    """给 /模式 预览用：条目数、正文字数、注入块字数。"""
    picked = list(entries)
    body = join_body(picked)
    block = build_worldbook_block(picked, mode, max_chars, include_hint)
    return {
        "mode": normalize_mode(mode),
        "entries": len(picked),
        "chars": len(body),
        "source_chars": source_chars(picked),
        "block_chars": len(block),
        "truncated": TRUNCATE_MARK in block,
        "uids": [_norm_uid(e.get("uid")) for e in picked],
    }
