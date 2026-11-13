"""按 unified_msg_origin 持久化模式与会话指针。

state.json 结构::

    {
      "version": 1,
      "sessions": {
        "<unified_msg_origin>": {
          "mode": "normal|tavern|blend",
          "conv_ids": {"normal": "uuid", "tavern": "uuid", "blend": "uuid"},
          "updated_at": "2026-11-12T23:00:00+08:00"
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

STATE_VERSION = 1


class StateStore:
    """极小的状态仓库：内存字典 + 原子落盘。"""

    def __init__(self, path: str | Path, default_mode: str = "normal") -> None:
        self.path = Path(path)
        self.default_mode = default_mode or "normal"
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"version": STATE_VERSION, "sessions": {}}
        self.load()

    # ---------------- 读写 ----------------
    def load(self) -> bool:
        """读取 state.json；文件缺失/损坏时保持空状态并返回 False。"""
        with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8-sig")
            except FileNotFoundError:
                return False
            except OSError:
                return False
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                return False
            if not isinstance(data, dict):
                return False
            sessions = data.get("sessions")
            if not isinstance(sessions, dict):
                sessions = {}
            clean: dict[str, dict] = {}
            for umo, val in sessions.items():
                if not isinstance(val, dict):
                    continue
                clean[str(umo)] = {
                    "mode": str(val.get("mode") or self.default_mode),
                    "conv_ids": {
                        str(k): str(v)
                        for k, v in (val.get("conv_ids") or {}).items()
                        if v
                    },
                    "updated_at": val.get("updated_at"),
                }
            self._data = {"version": STATE_VERSION, "sessions": clean}
            return True

    def save(self) -> bool:
        """原子写盘（tmp + os.replace）。失败返回 False，不抛异常。"""
        with self._lock:
            payload = json.dumps(self._data, ensure_ascii=False, indent=2)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(
                    prefix=".state-", suffix=".json", dir=str(self.path.parent)
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write(payload)
                    os.replace(tmp, self.path)
                finally:
                    if os.path.exists(tmp):
                        try:
                            os.unlink(tmp)
                        except OSError:
                            pass
                return True
            except OSError:
                return False

    # ---------------- 会话级操作 ----------------
    def _session(self, umo: str) -> dict:
        sessions = self._data.setdefault("sessions", {})
        sess = sessions.get(umo)
        if not isinstance(sess, dict):
            sess = {"mode": self.default_mode, "conv_ids": {}}
            sessions[umo] = sess
        sess.setdefault("mode", self.default_mode)
        if not isinstance(sess.get("conv_ids"), dict):
            sess["conv_ids"] = {}
        return sess

    def get_mode(self, umo: str) -> str:
        with self._lock:
            return str(self._session(umo).get("mode") or self.default_mode)

    def set_mode(self, umo: str, mode: str) -> bool:
        """返回是否真的发生了变化。"""
        with self._lock:
            sess = self._session(umo)
            changed = sess.get("mode") != mode
            sess["mode"] = mode
            sess["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            return changed

    def get_conv_id(self, umo: str, mode: str) -> str | None:
        with self._lock:
            cid = self._session(umo).get("conv_ids", {}).get(mode)
            return str(cid) if cid else None

    def set_conv_id(self, umo: str, mode: str, conv_id: str) -> None:
        with self._lock:
            if not conv_id:
                return
            sess = self._session(umo)
            sess["conv_ids"][mode] = str(conv_id)
            sess["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    def clear_conv_id(self, umo: str, mode: str) -> None:
        with self._lock:
            self._session(umo).get("conv_ids", {}).pop(mode, None)

    def session(self, umo: str) -> dict:
        """返回会话状态副本。"""
        with self._lock:
            sess = self._session(umo)
            return {
                "mode": str(sess.get("mode") or self.default_mode),
                "conv_ids": dict(sess.get("conv_ids") or {}),
                "updated_at": sess.get("updated_at"),
            }

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))
