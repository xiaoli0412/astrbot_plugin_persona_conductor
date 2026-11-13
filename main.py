"""人格调度器 — Persona Conductor for AstrBot.

三种模式，按 unified_msg_origin 记录、按模式隔离会话：

  normal  正常对话 —— 只用 AstrBot 现有 persona，不注入世界书
  tavern  酒馆模式 —— 注入完整世界书（角色卡驱动）
  blend   融合模式 —— persona + 精简世界书（剔除重复的角色档案，默认 uid=015）

注入走 req.extra_user_content_parts + TextPart.mark_as_temp()：
不写 system_prompt（不污染人格、不破坏前缀缓存），也不落会话历史。
"""

from __future__ import annotations

import functools
import json
import sys
from pathlib import Path

# --- 关键：入口把插件目录放进 sys.path，子包用绝对导入 ---
# 入口被 AstrBot 加载成 data.plugins.<dir>.main，不是包，
# 因此 `from .conductor import ...` 会报 No module named 'data.plugins.xxx.conductor'。
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from astrbot.api import AstrBotConfig, logger  # noqa: E402
from astrbot.api.event import AstrMessageEvent, filter  # noqa: E402
from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.api.star import Context, Star, register  # noqa: E402
from astrbot.core.agent.message import TextPart  # noqa: E402

from conductor.inject import (  # noqa: E402
    MODE_DESC,
    MODE_LABELS,
    MODES,
    build_worldbook_block,
    load_worldbook,
    normalize_mode,
    select_entries,
    worldbook_stats,
)
from conductor.store import StateStore  # noqa: E402

PLUGIN_NAME = "astrbot_plugin_persona_conductor"
PLUGIN_VERSION = "0.1.0"
DISPLAY_NAME = "人格调度"

DATA_DIR = Path("/AstrBot/data/plugin_data") / PLUGIN_NAME
STATE_FILE = DATA_DIR / "state.json"
WORLDBOOK_FILE = _PLUGIN_DIR / "worldbook" / "changqingteng.json"
CONFIG_CANDIDATES = (
    Path("/AstrBot/data/config") / f"{PLUGIN_NAME}_config.json",
    _PLUGIN_DIR / "_conf_schema.json",
)

WORLDBOOK_TAG = "<worldbook "
DEFAULT_PRIORITY = 200


def _read_priority(default: int = DEFAULT_PRIORITY) -> int:
    """装饰器优先级在 import 时就定了，所以在 import 期从配置里读一次。

    cmd_config 系文件历史上带 BOM，统一 utf-8-sig 读。
    """
    for path in CONFIG_CANDIDATES[:1]:
        try:
            cfg = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        try:
            value = int(cfg.get("inject_priority", default))
        except (TypeError, ValueError):
            return default
        return max(1, min(1000, value))
    return default


_PRIORITY = _read_priority()


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@register(
    PLUGIN_NAME,
    "xiaoli0412",
    "人格调度：正常对话 / 酒馆 / 融合 三模式切换，世界书按模式注入",
    PLUGIN_VERSION,
)
class PersonaConductor(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.store: StateStore | None = None
        self.worldbook: list[dict] = []
        self._ready = False

    # ---------------- 配置 ----------------
    def _cfg(self, key: str, default):
        value = self.config.get(key, default) if hasattr(self.config, "get") else default
        return default if value is None else value

    @property
    def default_mode(self) -> str:
        return normalize_mode(self._cfg("default_mode", "normal"), "normal")

    @property
    def max_chars(self) -> int:
        return max(0, _as_int(self._cfg("max_worldbook_chars", 24000), 24000))

    @property
    def inject_hint(self) -> bool:
        return _as_bool(self._cfg("inject_hint", True), True)

    def _exclude_uids(self, mode: str):
        if mode == "tavern":
            return self._cfg("tavern_exclude_uids", "")
        if mode == "blend":
            return self._cfg("blend_exclude_uids", "015")
        return ""

    # ---------------- 生命周期 ----------------
    async def initialize(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # 只读挂载等
            logger.warning(f"[{DISPLAY_NAME}] 数据目录不可写: {exc}")

        self.store = StateStore(STATE_FILE, default_mode=self.default_mode)
        try:
            self.worldbook = load_worldbook(WORLDBOOK_FILE)
        except (OSError, ValueError) as exc:
            self.worldbook = []
            logger.error(f"[{DISPLAY_NAME}] 世界书加载失败: {exc}")

        tavern = select_entries(self.worldbook, "tavern", self._exclude_uids("tavern"))
        blend = select_entries(self.worldbook, "blend", self._exclude_uids("blend"))
        self._ready = True
        logger.info(
            "[%s] 已就绪 | 模式=%s | 世界书 %d 条 | tavern %d 条/%d 字 | blend %d 条/%d 字 | priority=%d",
            DISPLAY_NAME,
            self.default_mode,
            len(self.worldbook),
            len(tavern),
            worldbook_stats(tavern, "tavern", self.max_chars, self.inject_hint)["chars"],
            len(blend),
            worldbook_stats(blend, "blend", self.max_chars, self.inject_hint)["chars"],
            _PRIORITY,
        )
        self._log_handler_binding()
        # 落一次盘：既确认数据目录可写，也让 state.json 一开始就存在
        if not self.store.save():
            logger.warning(f"[{DISPLAY_NAME}] state.json 写入失败（数据目录不可写？）")
        self._register_webui()

    def _register_webui(self) -> None:
        """PATCHED(local): 注册 WebUI 模式切换面板。"""
        try:
            reg = getattr(self.context, "register_web_api", None)
            if not reg:
                return
            reg("astrbot_plugin_persona_conductor/overview", self.api_overview,
                ["GET"], "人格调度：模式与统计")
            reg("astrbot_plugin_persona_conductor/switch", self.api_switch,
                ["POST"], "人格调度：切换会话模式")
            logger.info("[人格调度] WebUI API 已注册 2 条")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[人格调度] WebUI API 注册失败：%s", exc)

    def _log_handler_binding(self) -> None:
        """启动时自检：装饰器注册的 handler 是否已被 star_manager 绑到实例上。

        AstrBot 按 module_path 查 handler 再 partial 绑定；模块路径不一致时
        handler 会保持裸函数，调用时报 TypeError。这里把结果直接写进日志。
        """
        try:
            from astrbot.core.star.star_handler import star_handlers_registry
        except Exception as exc:  # 老版本没有该注册表
            logger.debug(f"[{DISPLAY_NAME}] 无法读取 handler 注册表: {exc}")
            return
        try:
            handlers = star_handlers_registry.get_handlers_by_module_name(__name__)
        except Exception as exc:
            logger.warning(f"[{DISPLAY_NAME}] 读取 handler 失败: {exc}")
            return

        bound = 0
        detail = []
        for h in handlers:
            is_bound = isinstance(getattr(h, "handler", None), functools.partial)
            bound += 1 if is_bound else 0
            event = getattr(h, "event_type", None)
            event = getattr(event, "name", str(event))
            detail.append(f"{getattr(h, 'handler_name', '?')}[{event}]{'✔' if is_bound else '✘'}")

        logger.info(
            "[%s] handler 绑定自检：注册 %d 个 / 已绑定实例 %d 个 | %s | module=%s",
            DISPLAY_NAME,
            len(handlers),
            bound,
            ", ".join(detail) or "无",
            __name__,
        )

    # ------------------------------------------------ WebUI（PATCHED local）
    async def api_overview(self):
        """当前模式 + 各模式统计 + 会话列表。"""
        snap = {}
        try:
            snap = self._store.snapshot() or {}
        except Exception:
            pass
        modes = {}
        for m in ("normal", "tavern", "blend"):
            try:
                st = self._stats(m)
            except Exception:
                st = {}
            modes[m] = st
        return {
            "ok": True,
            "data": {
                "default_mode": self.default_mode,
                "max_chars": self.max_chars,
                "inject_hint": self.inject_hint,
                "worldbook_total": len(getattr(self, "_entries", {}) or {}),
                "modes": modes,
                "sessions": snap.get("sessions", snap) if isinstance(snap, dict) else {},
            },
        }

    async def api_switch(self):
        """切换某个会话的模式。body: {umo, mode}"""
        from astrbot.api.web import request  # type: ignore
        try:
            umo = str(request.json.get("umo") or "").strip()
            mode = str(request.json.get("mode") or "").strip()
        except Exception:
            return {"ok": False, "error": "缺少参数"}
        if mode not in ("normal", "tavern", "blend"):
            return {"ok": False, "error": f"未知模式 {mode}"}
        if not umo:
            return {"ok": False, "error": "缺少 umo"}
        try:
            msg = await self._switch_mode(umo, mode)
            return {"ok": True, "data": {"message": msg, "mode": mode}}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def terminate(self) -> None:
        if self.store is not None:
            self.store.save()

    # ---------------- 注入钩子 ----------------
    @filter.on_llm_request(priority=_PRIORITY)
    async def inject_worldbook(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._ready or self.store is None:
            return
        if not _as_bool(self._cfg("enabled", True), True):
            return

        umo = self._umo(event)
        mode = normalize_mode(self.store.get_mode(umo), self.default_mode)
        if mode == "normal":
            return

        # 幂等：同一条请求里已经有世界书块就不再追加
        try:
            for part in req.extra_user_content_parts or []:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text.startswith(WORLDBOOK_TAG):
                    return
        except Exception:  # 理论上不会触发，兜底避免阻断请求
            pass

        entries = select_entries(self.worldbook, mode, self._exclude_uids(mode))
        block = build_worldbook_block(entries, mode, self.max_chars, self.inject_hint)
        if not block:
            return

        try:
            req.extra_user_content_parts.append(TextPart(text=block).mark_as_temp())
        except Exception as exc:
            logger.error(f"[{DISPLAY_NAME}] 注入失败: {exc}")
            return

        if _as_bool(self._cfg("log_inject", True), True):
            logger.info(
                "[%s] 注入 worldbook | mode=%s | entries=%d | chars=%d | umo=%s",
                DISPLAY_NAME,
                mode,
                len(entries),
                len(block),
                umo,
            )

    # ---------------- 指令 ----------------
    @filter.command("模式", alias={"mode"}, priority=10)
    async def cmd_mode(self, event: AstrMessageEvent):
        args = self._args(event)
        umo = self._umo(event)

        if not args:
            yield event.plain_result(self._status_text(umo))
            return

        token = args[0].strip().lower()

        if token in ("预览", "preview", "p", "status", "状态"):
            yield event.plain_result(self._preview_text(umo))
            return

        if token in ("帮助", "help", "?", "？"):
            yield event.plain_result(self._help_text())
            return

        target = normalize_mode(token, default="")
        if target not in MODES:
            yield event.plain_result(
                f"未知模式「{args[0]}」。\n{self._help_text()}"
            )
            return

        text = await self._switch_mode(umo, target)
        yield event.plain_result(text)

    # ---------------- 内部 ----------------
    @staticmethod
    def _umo(event: AstrMessageEvent) -> str:
        umo = getattr(event, "unified_msg_origin", "") or ""
        return str(umo)

    @staticmethod
    def _args(event: AstrMessageEvent) -> list[str]:
        raw = (getattr(event, "message_str", "") or "").strip()
        tokens = raw.split()
        return tokens[1:] if tokens else []

    def _entry_count(self, mode: str) -> int:
        return len(select_entries(self.worldbook, mode, self._exclude_uids(mode)))

    def _stats(self, mode: str) -> dict:
        entries = select_entries(self.worldbook, mode, self._exclude_uids(mode))
        return worldbook_stats(entries, mode, self.max_chars, self.inject_hint)

    def _status_text(self, umo: str) -> str:
        mode = normalize_mode(
            self.store.get_mode(umo) if self.store else self.default_mode,
            self.default_mode,
        )
        st = self._stats(mode)
        cid = self.store.get_conv_id(umo, mode) if self.store else None
        lines = [
            f"🎭 当前模式：{MODE_LABELS[mode]}（{mode}）",
            f"　 世界书：{st['entries']} 条 / {st['chars']} 字"
            if mode != "normal"
            else "　 世界书：不注入（0 条）",
            f"　 会话指针：{'已绑定 ' + cid[:8] + '…' if cid else '未绑定（下次切换时创建）'}",
            "",
            "可用模式：",
        ]
        for m in MODES:
            s = self._stats(m)
            lines.append(f"　 /模式 {m}（{MODE_LABELS[m]}）— {MODE_DESC[m]}")
            if m != "normal":
                lines.append(f"　　 → {s['entries']} 条 / {s['chars']} 字")
        lines.append("　 /模式 预览 — 看当前模式会注入多少内容")
        return "\n".join(lines)

    def _help_text(self) -> str:
        lines = ["用法：", "　 /模式 — 查看当前模式", "　 /模式 normal（正常）— 不注入世界书"]
        lines.append("　 /模式 tavern（酒馆）— 完整世界书")
        lines.append("　 /模式 blend（融合）— persona + 精简世界书")
        lines.append("　 /模式 预览 — 只统计，不发请求")
        return "\n".join(lines)

    def _preview_text(self, umo: str) -> str:
        mode = normalize_mode(
            self.store.get_mode(umo) if self.store else self.default_mode,
            self.default_mode,
        )
        st = self._stats(mode)
        if mode == "normal":
            return (
                f"🔍 预览（{MODE_LABELS[mode]} / {mode}）\n"
                "　 条目：0 条\n　 正文字数：0\n　 注入块：0 字\n"
                "　 说明：该模式不注入任何世界书，只用现有 persona。"
            )
        uids = st["uids"]
        uid_span = f"{uids[0]} … {uids[-1]}" if len(uids) > 1 else (uids[0] if uids else "-")
        lines = [
            f"🔍 预览（{MODE_LABELS[mode]} / {mode}）",
            f"　 条目：{st['entries']} 条（uid {uid_span}）",
            f"　 正文字数：{st['chars']}",
            f"　 注入块：{st['block_chars']} 字（含 <worldbook> 标签"
            + ("与约束句" if self.inject_hint else "")
            + "）",
            f"　 素材原始字数（含已剥掉的 YAML 头）：{st['source_chars']}",
            f"　 截断：{'是' if st['truncated'] else '否'}（max_worldbook_chars={self.max_chars}）",
            f"　 排除 uid：{self._exclude_uids(mode) or '无'}",
        ]
        return "\n".join(lines)

    async def _switch_mode(self, umo: str, target: str) -> str:
        if self.store is None:
            return "状态存储未就绪，稍后再试。"

        previous = normalize_mode(self.store.get_mode(umo), self.default_mode)
        self.store.set_mode(umo, target)
        conv_note = "会话未变（conversation_manager 不可用）"
        try:
            cid, action = await self._activate_conversation(umo, target)
            if action == "created":
                conv_note = f"已新建专属会话 [{target}] {cid[:8]}…（历史与其它模式隔离）"
            elif action == "adopted":
                conv_note = f"已接管当前会话作为 [{target}] 专属（保留原有历史）{cid[:8]}…"
            elif action == "switched":
                conv_note = f"已切回专属会话 [{target}] {cid[:8]}…"
        except Exception as exc:
            logger.error(f"[{DISPLAY_NAME}] 切换会话失败: {exc}")
            conv_note = f"切换会话失败：{exc}"
        self.store.save()

        st = self._stats(target)
        head = (
            f"✅ 已切到【{MODE_LABELS[target]} / {target}】"
            if previous != target
            else f"ℹ️ 当前已经是【{MODE_LABELS[target]} / {target}】"
        )
        if target == "normal":
            wb = "　 世界书：不注入（只用现有 persona）"
        else:
            wb = f"　 世界书：{st['entries']} 条 / {st['chars']} 字"
        return "\n".join([head, wb, f"　 {conv_note}"])

    async def _activate_conversation(self, umo: str, mode: str):
        """拿到该模式的专属 conversation 并切过去；没有就新建。

        返回 (conversation_id, action)，action ∈ switched|adopted|created|none。
        """
        cm = getattr(self.context, "conversation_manager", None)
        if cm is None:
            return None, "none"

        stored = self.store.get_conv_id(umo, mode)
        if stored:
            conv = None
            try:
                conv = await cm.get_conversation(umo, stored)
            except TypeError:
                conv = await cm.get_conversation(stored)
            except Exception as exc:
                logger.warning(f"[{DISPLAY_NAME}] 读取会话 {stored[:8]} 失败: {exc}")
            if conv is not None:
                await cm.switch_conversation(umo, stored)
                return stored, "switched"
            self.store.clear_conv_id(umo, mode)

        # 首次切到 normal：如果当前会话还没被别的模式占用，直接接管，
        # 这样插件装上以前的历史不会凭空消失（normal 就是「原样」）。
        if mode == "normal":
            curr = None
            try:
                curr = await cm.get_curr_conversation_id(umo)
            except Exception as exc:
                logger.debug(f"[{DISPLAY_NAME}] 读取当前会话失败: {exc}")
            bound = set(self.store.session(umo).get("conv_ids", {}).values())
            if curr and curr not in bound:
                self.store.set_conv_id(umo, mode, curr)
                return curr, "adopted"

        platform_id = umo.split(":", 1)[0] if umo else None
        cid = await cm.new_conversation(
            umo,
            platform_id=platform_id,
            title=f"[{mode}] {MODE_LABELS[mode]}",
        )
        self.store.set_conv_id(umo, mode, cid)
        return cid, "created"
