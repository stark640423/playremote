"""macOS 的輸出層：模擬按鍵 (CGEvent)、打字、啟動程式。需要「輔助使用」權限。

組合鍵寫法與 Windows 版相同，用 + 連接，例如 "cmd+shift+v"、"ctrl+alt+t"、"fn"、"enter"。
修飾鍵別名: cmd/command/win, ctrl/control, alt/opt/option, shift, fn/globe。
"""
from __future__ import annotations

import subprocess
import time

import Quartz

# 虛擬鍵碼 (ANSI 鍵盤)
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
    ".": 47, "`": 50,
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "delete": 51, "esc": 53, "escape": 53,
    "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113, "f16": 106, "f17": 64,
    "f18": 79, "f19": 80, "f20": 90,
    "capslock": 57,
}
MODIFIERS = {
    "cmd": (55, Quartz.kCGEventFlagMaskCommand), "command": (55, Quartz.kCGEventFlagMaskCommand),
    "win": (55, Quartz.kCGEventFlagMaskCommand), "rcmd": (54, Quartz.kCGEventFlagMaskCommand),
    "ctrl": (59, Quartz.kCGEventFlagMaskControl), "control": (59, Quartz.kCGEventFlagMaskControl),
    "alt": (58, Quartz.kCGEventFlagMaskAlternate), "opt": (58, Quartz.kCGEventFlagMaskAlternate),
    "option": (58, Quartz.kCGEventFlagMaskAlternate), "ropt": (61, Quartz.kCGEventFlagMaskAlternate),
    "shift": (56, Quartz.kCGEventFlagMaskShift), "rshift": (60, Quartz.kCGEventFlagMaskShift),
    "fn": (63, Quartz.kCGEventFlagMaskSecondaryFn), "globe": (63, Quartz.kCGEventFlagMaskSecondaryFn),
}
# 媒體鍵 (NSEvent systemDefined, subtype 8)
MEDIA_KEYS = {"volume_up": 0, "volume_down": 1, "mute": 7, "play_pause": 16, "next": 17, "prev": 18,
              "brightness_up": 2, "brightness_down": 3}


class Combo:
    def __init__(self, mods: list[tuple[int, int]], key: int | None, media: int | None = None):
        self.mods = mods
        self.key = key
        self.media = media


def parse_combo(combo: str) -> Combo:
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError(f"空的組合鍵: {combo!r}")
    mods: list[tuple[int, int]] = []
    key = None
    media = None
    for p in parts:
        if p in MODIFIERS:
            mods.append(MODIFIERS[p])
        elif p in MEDIA_KEYS:
            media = MEDIA_KEYS[p]
        elif p in KEYCODES:
            if key is not None:
                raise ValueError(f"組合鍵只能有一個主鍵: {combo!r}")
            key = KEYCODES[p]
        else:
            raise ValueError(f"不認得的鍵名 {p!r} (在 {combo!r})")
    if key is None and media is None and not mods:
        raise ValueError(f"組合鍵沒有內容: {combo!r}")
    return Combo(mods, key, media)


def _post_key(keycode: int, down: bool, flags: int = 0):
    ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
    Quartz.CGEventSetFlags(ev, flags if down else 0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _post_media(key: int, down: bool):
    import AppKit  # 延後載入，避免沒用到媒體鍵時多載一個框架
    flags = 0xA00 if down else 0xB00
    data1 = (key << 16) | (flags)
    ev = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
        AppKit.NSEventTypeSystemDefined, (0, 0), 0xA00 if down else 0xB00, 0, 0, None, 8, data1, -1)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev.CGEvent())


def key_down(c: Combo):
    flags = 0
    for code, mask in c.mods:
        flags |= mask
        _post_key(code, True, flags)
    if c.key is not None:
        _post_key(c.key, True, flags)
    if c.media is not None:
        _post_media(c.media, True)


def key_up(c: Combo):
    if c.media is not None:
        _post_media(c.media, False)
    if c.key is not None:
        _post_key(c.key, False)
    for code, _mask in reversed(c.mods):
        _post_key(code, False)


def send_keys(combo: str):
    c = parse_combo(combo)
    key_down(c)
    time.sleep(0.02)
    key_up(c)


def type_text(text: str):
    """用 CGEvent 直接送 Unicode 字串，中文也可以。"""
    for i in range(0, len(text), 20):
        chunk = text[i:i + 20]
        ev = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
        Quartz.CGEventKeyboardSetUnicodeString(ev, len(chunk), chunk)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        ev = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.01)


def run(command: str):
    """啟動程式。
    - 以 .app 結尾或存在於 /Applications 的名稱 → open -a
    - 其他 → 當 shell 指令執行 (不等待)
    """
    cmd = command.strip()
    if cmd.endswith(".app") or cmd.startswith("/Applications/"):
        subprocess.Popen(["open", "-a", cmd])
        return
    import os
    for d in ("/Applications", "/System/Applications", os.path.expanduser("~/Applications")):
        if os.path.exists(os.path.join(d, cmd + ".app")) or os.path.exists(os.path.join(d, cmd)):
            subprocess.Popen(["open", "-a", cmd])
            return
    subprocess.Popen(cmd, shell=True)
