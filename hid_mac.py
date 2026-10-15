"""macOS：用 IOHIDManager 讀 PlayRemote USB 接收器的 HID 報告，解成鍵名事件。

接收器 (VID 0x0610 / PID 0x1000) 有四個 HID 介面，報告格式見 NOTES.md：
- page 1 / usage 5 (遊戲控制器)：report 0xC5 內含鍵盤碼 → 方向、數字、退格；0xC4 是閒置心跳
- page 12 / usage 1 (Consumer)：report 2，usage 16 bit → ok/back/home/menu/power/音量/播放/search
- page 1 / usage 2 (滑鼠)：陀螺儀位移 + 滾輪 (只取滾輪當事件，位移交給系統)
- page 0xFF00 (vendor)：忽略

獨占 (seize)：對有設 block 的介面用 kIOHIDOptionsTypeSeizeDevice 打開，系統就收不到那個介面的任何報告，
驅動再把沒被擋的鍵用 CGEvent 補送回去。滑鼠介面永遠不獨占。
需要「輸入監控」權限。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import time
from dataclasses import dataclass
from typing import Callable, Optional

iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))

kCFStringEncodingUTF8 = 0x08000100
kCFNumberIntType = 9
kIOHIDOptionsTypeNone = 0
kIOHIDOptionsTypeSeizeDevice = 1

cf.CFStringCreateWithCString.restype = ctypes.c_void_p
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
cf.CFNumberCreate.restype = ctypes.c_void_p
cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
cf.CFNumberGetValue.restype = ctypes.c_bool
cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
cf.CFDictionaryCreateMutable.restype = ctypes.c_void_p
cf.CFDictionaryCreateMutable.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
cf.CFDictionarySetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
cf.CFRunLoopRunInMode.restype = ctypes.c_int32
cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
kCFRunLoopDefaultMode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
kCFTypeDictionaryKeyCallBacks = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
kCFTypeDictionaryValueCallBacks = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")

iokit.IOHIDManagerCreate.restype = ctypes.c_void_p
iokit.IOHIDManagerCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDManagerSetDeviceMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerRegisterDeviceMatchingCallback.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerRegisterDeviceRemovalCallback.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerOpen.restype = ctypes.c_int
iokit.IOHIDManagerOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDDeviceOpen.restype = ctypes.c_int
iokit.IOHIDDeviceOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDDeviceClose.restype = ctypes.c_int
iokit.IOHIDDeviceClose.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDDeviceScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDDeviceRegisterInputReportCallback.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
                                                         ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDDeviceGetProperty.restype = ctypes.c_void_p
iokit.IOHIDDeviceGetProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerClose.restype = ctypes.c_int
iokit.IOHIDManagerClose.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDManagerCopyDevices.restype = ctypes.c_void_p
iokit.IOHIDManagerCopyDevices.argtypes = [ctypes.c_void_p]
iokit.IOHIDManagerRegisterInputReportCallback.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8),
                                                          ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
cf.CFSetGetCount.restype = ctypes.c_long
cf.CFSetGetCount.argtypes = [ctypes.c_void_p]
cf.CFSetGetValues.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

iokit.IOHIDCheckAccess.restype = ctypes.c_int
iokit.IOHIDCheckAccess.argtypes = [ctypes.c_int]
iokit.IOHIDRequestAccess.restype = ctypes.c_bool
iokit.IOHIDRequestAccess.argtypes = [ctypes.c_int]
kIOHIDRequestTypeListenEvent = 1          # 「輸入監控」
kIOHIDAccessTypeGranted = 0

ERR_EXCLUSIVE = 0xE00002C5                # kIOReturnExclusiveAccess：被別的程式獨占
ERR_NOT_PERMITTED = 0xE00002E2            # kIOReturnNotPermitted：沒有輸入監控權限
ERR_NOT_PRIVILEGED = 0xE00002C1           # kIOReturnNotPrivileged：不允許獨占（macOS 不讓第三方獨占已配對鍵盤）


def input_monitoring_granted() -> bool:
    return iokit.IOHIDCheckAccess(kIOHIDRequestTypeListenEvent) == kIOHIDAccessTypeGranted


def request_input_monitoring() -> bool:
    """跳出系統的「輸入監控」授權提示（只會在第一次時跳）；回傳目前是否已授權。"""
    return bool(iokit.IOHIDRequestAccess(kIOHIDRequestTypeListenEvent))


DeviceCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
ReportCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint8), ctypes.c_long)

# ---- 鍵名對照 (與 Windows 版 rawinput.py 一致) ----
KEY_NAMES_BY_KEYBOARD = {0x52: "up", 0x51: "down", 0x50: "left", 0x4F: "right",
                         0x1E: "1", 0x1F: "2", 0x20: "3", 0x21: "4", 0x22: "5", 0x23: "6", 0x24: "7",
                         0x25: "8", 0x26: "9", 0x27: "0", 0x2A: "backspace", 0x28: "enter", 0x29: "esc"}
KEY_NAMES_BY_CONSUMER = {0x41: "ok", 0x224: "back", 0x223: "home", 0x40: "menu", 0x30: "power",
                         0xE9: "volume_up", 0xEA: "volume_down", 0xE2: "mute", 0xCD: "play_pause",
                         0x221: "search"}
# 補送回系統時用的鍵 (macio 的鍵名)
REPLAY_KEYS = {"up": "up", "down": "down", "left": "left", "right": "right", "backspace": "backspace",
               "enter": "enter", "esc": "esc", **{str(d): str(d) for d in range(10)},
               "ok": "enter", "volume_up": "volume_up", "volume_down": "volume_down", "mute": "mute",
               "play_pause": "play_pause"}

IFACE_KEYBOARD = (0x01, 0x05)   # 遊戲控制器介面 (內含鍵盤報告)
IFACE_CONSUMER = (0x0C, 0x01)
IFACE_MOUSE = (0x01, 0x02)
IFACE_NAMES = {IFACE_KEYBOARD: "keyboard", IFACE_CONSUMER: "consumer", IFACE_MOUSE: "mouse"}
KEYS_OF_IFACE = {"keyboard": set(KEY_NAMES_BY_KEYBOARD.values()),
                 "consumer": set(KEY_NAMES_BY_CONSUMER.values()),
                 "mouse": {"wheel_up", "wheel_down"}}

# 藍牙配對模式：VID 0x2D01 / PID 0x5000，只有一個鍵盤介面 (page 1 / usage 6)，
# 全部的鍵都走這個介面，用 report id 區分 (實測見 docs/FIRMWARE_HANDOFF.md)：
#   id=1 鍵盤 (9B): 01 mod rsvd <鍵碼> …，鍵碼在 byte[3] 之後
#   id=2 Consumer (6B): 02 LL HH …，usage 16-bit little-endian
#   id=3 滑鼠 (7B): 03 <鍵> XL XH YL YH，陀螺儀位移 (signed LE) + 滾輪
BLE_VID, BLE_PID = 0x2D01, 0x5000
BLE_REPORT_KEYBOARD, BLE_REPORT_CONSUMER, BLE_REPORT_MOUSE = 1, 2, 3


@dataclass
class RemoteEvent:
    kind: str          # "key" | "wheel" | "motion"
    name: str
    down: bool
    iface: str         # "keyboard" | "consumer" | "mouse"
    seized: bool       # 這個介面是否被獨占 (True 時系統沒收到，需要補送)
    dx: int = 0        # kind=="motion" 時的滑鼠相對位移 (藍牙陀螺儀)
    dy: int = 0


def _s16(raw: bytes, off: int) -> int:
    """從 off 取 little-endian 有號 16-bit (藍牙滑鼠陀螺儀位移)。"""
    if off + 2 > len(raw):
        return 0
    v = raw[off] | (raw[off + 1] << 8)
    return v - 0x10000 if v >= 0x8000 else v


def _cfstr(s: str):
    return cf.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8)


def _cfnum(v: int):
    c = ctypes.c_int(v)
    return cf.CFNumberCreate(None, kCFNumberIntType, ctypes.byref(c))


def _dev_int(dev, key: str) -> int:
    ref = iokit.IOHIDDeviceGetProperty(dev, _cfstr(key))
    if not ref:
        return -1
    out = ctypes.c_int(0)
    cf.CFNumberGetValue(ref, kCFNumberIntType, ctypes.byref(out))
    return out.value


class HidListener:
    """監看接收器；on_event(RemoteEvent)。seize_ifaces 是要獨占的介面名稱集合。"""

    def __init__(self, on_event: Callable[[RemoteEvent], None], seize_ifaces: set[str] = frozenset(),
                 vid: int = 0x0610, pid: int = 0x1000, on_log: Optional[Callable[[str], None]] = None,
                 on_raw: Optional[Callable[[str, bytes], None]] = None, transport: str = "usb"):
        self.on_event = on_event
        self.transport = transport                 # "usb" (接收器) | "ble" (系統配對)
        self._ble_seized = False
        self._last_ble_scan = 0.0
        # USB：滑鼠介面獨立，永不獨占；藍牙：全部擠在一個介面，要擋鍵就得連滑鼠一起獨占並補送位移
        self.seize = set(seize_ifaces) if transport == "ble" else set(seize_ifaces) - {"mouse"}
        self.vid, self.pid = vid, pid
        self.log = on_log or (lambda m: None)
        self.on_raw = on_raw
        self._bufs = []
        self._cbs = []
        self._devices: dict[int, dict] = {}
        self._pressed: dict[str, set[str]] = {}   # iface → 目前按住的鍵名
        self._mgr = None
        self.opened = False
        self.problems: set[str] = set()           # "permission" / "exclusive"

    @property
    def devices(self) -> list[dict]:
        if self.transport == "ble" and self._mgr:
            now = time.monotonic()
            if now - self._last_ble_scan > 0.5:        # 節流：最多每 0.5 秒重新列舉一次
                self._last_ble_scan = now
                self._ble_scan_and_open()              # 遙控器睡眠→喚醒後，重新開啟並更新狀態
        return list(self._devices.values())

    def start(self) -> bool:
        self._mgr = iokit.IOHIDManagerCreate(None, kIOHIDOptionsTypeNone)
        report_cb = ReportCB(self._on_report)
        add_cb = DeviceCB(self._on_add)
        rm_cb = DeviceCB(self._on_remove)
        self._cbs += [report_cb, add_cb, rm_cb]
        self._report_cb = report_cb
        if self.transport == "ble":
            # 藍牙是系統擁有的裝置：VendorID/ProductID 過濾會讓它列舉不到，manager 層級的
            # input report callback 也收不到。所以不過濾、列舉全部、用 Python 比對 VID，
            # 再「逐裝置」開啟並註冊 callback（實測這條才收得到報告）。
            self._ble_seized = bool(self.seize)
            iokit.IOHIDManagerSetDeviceMatching(self._mgr, None)
            self._ble_scan_and_open()
        else:
            match = cf.CFDictionaryCreateMutable(None, 0, ctypes.byref(kCFTypeDictionaryKeyCallBacks),
                                                 ctypes.byref(kCFTypeDictionaryValueCallBacks))
            cf.CFDictionarySetValue(match, _cfstr("VendorID"), _cfnum(self.vid))
            cf.CFDictionarySetValue(match, _cfstr("ProductID"), _cfnum(self.pid))
            iokit.IOHIDManagerSetDeviceMatching(self._mgr, match)
            iokit.IOHIDManagerRegisterDeviceMatchingCallback(self._mgr, add_cb, None)
            iokit.IOHIDManagerRegisterDeviceRemovalCallback(self._mgr, rm_cb, None)
            iokit.IOHIDManagerScheduleWithRunLoop(self._mgr, cf.CFRunLoopGetCurrent(), kCFRunLoopDefaultMode)
            # USB：不用 manager 開裝置，改成每個介面各自開 (才能決定哪些要獨占)
        self.opened = True
        return True

    def _ble_scan_and_open(self):
        """列舉所有 HID 裝置，對 VID 符合、還沒開的藍牙裝置逐一開啟並註冊 report callback。

        遙控器睡眠後指標失效會從 CopyDevices 消失；喚醒後是新物件，再被這裡開一次。
        """
        dset = iokit.IOHIDManagerCopyDevices(self._mgr)
        n = cf.CFSetGetCount(dset) if dset else 0
        if not n:
            # 裝置都不在了（遙控器睡眠/斷線）：清掉舊的，下次喚醒重開
            self._devices.clear()
            return
        arr = (ctypes.c_void_p * n)()
        cf.CFSetGetValues(dset, arr)
        present = set()
        for d in arr:
            if not d:
                continue
            if (_dev_int(d, "VendorID") & 0xffff) != (self.vid & 0xffff):
                continue
            key = int(d)
            present.add(key)
            if key in self._devices:
                continue                                  # 已經開過
            seize = self._ble_seized
            ret = iokit.IOHIDDeviceOpen(d, kIOHIDOptionsTypeSeizeDevice if seize else kIOHIDOptionsTypeNone)
            if ret != 0:
                code = ret & 0xffffffff
                if code == ERR_NOT_PERMITTED:
                    self.problems.add("permission")
                    self.log("無法讀取藍牙遙控器：沒有「輸入監控」權限")
                    return
                # macOS 不允許第三方獨占已配對的藍牙鍵盤（NotPrivileged / Exclusive）→ 退回一般監聽模式。
                # 收得到按鍵、動作照跑，但擋不掉原本的鍵（例如按 1 會同時打出 1）。
                if code in (ERR_NOT_PRIVILEGED, ERR_EXCLUSIVE) and seize:
                    self.problems.add("no_seize")
                    self.log("藍牙無法獨占鍵盤（macOS 限制），改用監聽模式：按鍵/動作/語音可用，但擋鍵無效")
                    seize = False
                    ret = iokit.IOHIDDeviceOpen(d, kIOHIDOptionsTypeNone)
                    if ret != 0:
                        self.log(f"開啟藍牙 HID 失敗 (0x{ret & 0xffffffff:x})")
                        continue
                else:
                    self.log(f"開啟藍牙 HID 失敗 (0x{code:x})")
                    continue
            buf = (ctypes.c_uint8 * 64)()
            self._bufs.append(buf)
            iokit.IOHIDDeviceRegisterInputReportCallback(d, buf, 64, self._report_cb, None)
            iokit.IOHIDDeviceScheduleWithRunLoop(d, cf.CFRunLoopGetCurrent(), kCFRunLoopDefaultMode)
            self._devices[key] = {"iface": "ble", "seized": seize, "ref": d}
            self.log("藍牙遙控器已連上" + ("（獨占）" if seize else ""))
        # 丟掉已經不在清單上的（睡眠/斷線）
        for key in list(self._devices):
            if key not in present:
                self._devices.pop(key, None)

    def stop(self):
        """關閉所有介面（解除獨占），之後按鍵恢復由系統處理。"""
        if self.transport == "ble":
            for info in list(self._devices.values()):   # 逐裝置關閉（解除獨占）
                try:
                    iokit.IOHIDDeviceClose(info["ref"], kIOHIDOptionsTypeNone)
                except Exception:  # noqa: BLE001
                    pass
        else:
            for dev in list(self._devices):
                try:
                    iokit.IOHIDDeviceClose(dev, kIOHIDOptionsTypeNone)
                except Exception:  # noqa: BLE001
                    pass
        self._devices.clear()
        self._pressed.clear()
        self.opened = False

    def pump(self, seconds: float = 0.05):
        """在呼叫端的執行緒跑一小段 run loop，處理 HID 回呼。"""
        cf.CFRunLoopRunInMode(kCFRunLoopDefaultMode, seconds, False)

    # ---- 回呼 ----
    def _on_add(self, ctx, result, sender, device):
        if self.transport == "ble":
            return                                       # 藍牙在 start() 用 manager 層級開啟，不走這裡
        page, usage = _dev_int(device, "PrimaryUsagePage"), _dev_int(device, "PrimaryUsage")
        iface = IFACE_NAMES.get((page, usage), f"page{page:x}/usage{usage:x}")
        seize = iface in self.seize
        ret = iokit.IOHIDDeviceOpen(device, kIOHIDOptionsTypeSeizeDevice if seize else kIOHIDOptionsTypeNone)
        if ret != 0:
            code = ret & 0xffffffff
            if code == ERR_NOT_PERMITTED:
                self.problems.add("permission")
                self.log(f"無法讀取 {iface}：沒有「輸入監控」權限")
                return
            if code == ERR_EXCLUSIVE:
                self.problems.add("exclusive")
                self.log(f"無法讀取 {iface}：被另一個程式佔用（是否還開著另一個 PlayRemote 或 playremote_mac.py？）")
            else:
                self.log(f"開啟 HID 介面 {iface} 失敗 (0x{code:x})")
            if seize:
                seize = False
                ret = iokit.IOHIDDeviceOpen(device, kIOHIDOptionsTypeNone)
                if ret != 0:
                    return
        buf = (ctypes.c_uint8 * 64)()
        self._bufs.append(buf)
        iokit.IOHIDDeviceRegisterInputReportCallback(device, buf, 64, self._report_cb, None)
        iokit.IOHIDDeviceScheduleWithRunLoop(device, cf.CFRunLoopGetCurrent(), kCFRunLoopDefaultMode)
        self._devices[device] = {"iface": iface, "seized": seize, "page": page, "usage": usage}
        self.log(f"HID 介面 {iface}" + ("（獨占）" if seize else ""))

    def _on_remove(self, ctx, result, sender, device):
        info = self._devices.pop(device, None)
        if info:
            self.log(f"HID 介面 {info['iface']} 已移除（接收器拔掉？）")

    def _on_report(self, ctx, result, sender, rtype, report_id, data, length):
        if self.transport == "ble":
            info = self._devices.get(int(sender)) if sender else None
            seized = info["seized"] if info else self._ble_seized
            self._on_ble_report(report_id, bytes(data[:length]), length, seized)
            return
        info = self._devices.get(sender)
        if not info:
            return
        raw = bytes(data[:length])
        iface, seized = info["iface"], info["seized"]
        if iface == "keyboard" and report_id != 0xC5:
            return                                      # 0xC4 閒置心跳等
        if self.on_raw:
            self.on_raw(iface, raw)
        if iface == "keyboard":
            codes = {b for b in raw[1:] if b}
            names = {KEY_NAMES_BY_KEYBOARD.get(c, f"key{c:02x}") for c in codes}
            self._diff(iface, names, seized)
        elif iface == "consumer":
            if length < 3:
                return
            usage = raw[1] | (raw[2] << 8)
            names = {KEY_NAMES_BY_CONSUMER.get(usage, f"consumer{usage:03x}")} if usage else set()
            self._diff(iface, names, seized)
        elif iface == "mouse":
            if length >= 6 and raw[5]:
                wheel = raw[5] if raw[5] < 0x80 else raw[5] - 0x100
                self.on_event(RemoteEvent("wheel", "wheel_up" if wheel > 0 else "wheel_down", True, iface, False))

    def _on_ble_report(self, report_id: int, raw: bytes, length: int, seized: bool):
        """藍牙單一介面：用 report id 分流成 keyboard / consumer / mouse 三種邏輯事件。"""
        if report_id == BLE_REPORT_KEYBOARD:
            if self.on_raw:
                self.on_raw("keyboard", raw)
            codes = {b for b in raw[3:length] if b}          # byte0=id,1=修飾,2=保留,之後才是鍵碼
            names = {KEY_NAMES_BY_KEYBOARD.get(c, f"key{c:02x}") for c in codes}
            self._diff("keyboard", names, seized)
        elif report_id == BLE_REPORT_CONSUMER:
            if length < 3:
                return
            if self.on_raw:
                self.on_raw("consumer", raw)
            usage = raw[1] | (raw[2] << 8)
            names = {KEY_NAMES_BY_CONSUMER.get(usage, f"consumer{usage:03x}")} if usage else set()
            self._diff("consumer", names, seized)
        elif report_id == BLE_REPORT_MOUSE:
            if self.on_raw:
                self.on_raw("mouse", raw)
            dx = _s16(raw, 2)
            dy = _s16(raw, 4)
            wheel = (raw[6] if raw[6] < 0x80 else raw[6] - 0x100) if length >= 7 else 0
            if wheel:
                self.on_event(RemoteEvent("wheel", "wheel_up" if wheel > 0 else "wheel_down", True, "mouse", seized))
            if seized and (dx or dy):
                # 介面被獨占 → 系統收不到陀螺儀位移，發 motion 事件讓驅動補送滑鼠移動
                self.on_event(RemoteEvent("motion", "move", True, "mouse", seized, dx=dx, dy=dy))

    def _diff(self, iface: str, now: set[str], seized: bool):
        prev = self._pressed.get(iface, set())
        for n in now - prev:
            self.on_event(RemoteEvent("key", n, True, iface, seized))
        for n in prev - now:
            self.on_event(RemoteEvent("key", n, False, iface, seized))
        self._pressed[iface] = now
