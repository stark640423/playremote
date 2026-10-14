"""單擊 / 雙擊 / 長按 的手勢判定（跨平台，從 playremote.py 的 _gesture_* 抽出）。

用法:
    gd = GestureDetector(loop, fire, double_ms=600, long_ms=500)
    gd.down(name, entry)   # entry = {"block":..., "single":..., "double":..., "long":...}
    gd.up(name, entry)
    gd.instant(name, entry)  # 沒有按下/放開之分的事件 (滾輪)

fire(name, entry, gesture) 會在判定完成時被呼叫，gesture ∈ {"single","double","long"}。

規則 (與 Windows 版一致):
- 只設單擊的鍵在「按下」當下就觸發。
- 有設雙擊的鍵，單擊要等雙擊窗過了才觸發。
- 有設長按的鍵，按住超過 long_ms 觸發長按並抑制單擊。
"""
from __future__ import annotations

import asyncio
from typing import Callable

GESTURES = ("single", "double", "long")
ACTION_KEYS = ("run", "keys", "text")
GESTURE_NAME = {"single": "單擊", "double": "雙擊", "long": "長按"}


def normalize_keys(raw: dict) -> dict:
    """把設定檔的按鍵表整理成 {鍵名: {block, single, double, long}}（與 playremote.py 相同）。"""
    out = {}
    for name, a in raw.items():
        if not isinstance(a, dict):
            continue
        entry = {"block": bool(a.get("block", False)), "single": None, "double": None, "long": None}
        if any(g in a for g in GESTURES):
            for g in GESTURES:
                act = a.get(g)
                entry[g] = act if isinstance(act, dict) and any(k in act for k in ACTION_KEYS) else None
        else:
            act = {k: v for k, v in a.items() if k in ACTION_KEYS}
            entry["single"] = act or None
        out[name] = entry
    return out


class GestureDetector:
    def __init__(self, loop: asyncio.AbstractEventLoop, fire: Callable[[str, dict, str], None],
                 double_ms: float = 600, long_ms: float = 500):
        self.loop = loop
        self.fire = fire
        self.double_ms = float(double_ms)
        self.long_ms = float(long_ms)
        self._state: dict[str, dict] = {}

    def _g(self, name: str) -> dict:
        return self._state.setdefault(name, {"pending_single": None, "long_timer": None,
                                             "long_fired": False, "ignore_up": False, "is_down": False})

    def instant(self, name: str, entry: dict):
        self.fire(name, entry, "single")

    def down(self, name: str, entry: dict):
        g = self._g(name)
        if g["is_down"]:                                # 自動重複的 down，忽略
            return
        g["is_down"] = True
        if g["pending_single"] is not None:             # 雙擊窗內的第二次按下
            g["pending_single"].cancel()
            g["pending_single"] = None
            g["ignore_up"] = True
            self.fire(name, entry, "double")
            return
        g["long_fired"] = False
        if entry.get("long"):
            g["long_timer"] = self.loop.call_later(self.long_ms / 1000, self._fire_long, name, entry)
        elif not entry.get("double"):
            g["ignore_up"] = True                       # 只有單擊: 按下就觸發
            self.fire(name, entry, "single")

    def _fire_long(self, name: str, entry: dict):
        g = self._g(name)
        g["long_timer"] = None
        g["long_fired"] = True
        self.fire(name, entry, "long")

    def up(self, name: str, entry: dict):
        g = self._g(name)
        g["is_down"] = False
        if g["long_timer"] is not None:
            g["long_timer"].cancel()
            g["long_timer"] = None
        if g["long_fired"]:
            g["long_fired"] = False
            return
        if g["ignore_up"]:
            g["ignore_up"] = False
            return
        if entry.get("double"):
            g["pending_single"] = self.loop.call_later(self.double_ms / 1000, self._fire_pending_single, name, entry)
        else:
            self.fire(name, entry, "single")

    def _fire_pending_single(self, name: str, entry: dict):
        self._g(name)["pending_single"] = None
        self.fire(name, entry, "single")
