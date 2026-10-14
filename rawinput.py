"""Windows Raw Input + 低階鍵盤 hook，只處理 PlayRemote 來的輸入。

兩個類別:
  RawInputListener  用 WM_INPUT 收鍵盤 / 滑鼠 / Consumer / 遊戲控制器事件，過濾到指定 VID 的裝置，
                    轉成 RemoteEvent 丟給 callback。
  KeyBlocker        WH_KEYBOARD_LL hook。實測的限制：hook 比 WM_INPUT 先跑，而且被 hook 擋掉的鍵盤事件
                    **不會**再產生 WM_INPUT，所以鍵盤 collection 的鍵 (數字、方向、退格) 在 hook 當下無法分辨來源。
                    目前做法:
                    - 鍵盤 collection 的鍵: Raw Input 看到遙控器按下後登記 held，之後自動重複的 down 全擋；
                      第一下會漏到目前視窗 (已知限制，要徹底解決需要核心層的過濾驅動，例如 Interception)。
                    - Consumer 鍵 (OK/返回/Home/選單): 它們的 HID 報告不受鍵盤 hook 影響，一定會有 WM_INPUT，
                      所以用「先擋、後補」: hook 先擋下被翻譯出來的 VK，Raw Input 說是遙控器就吞掉，
                      是實體鍵盤就用 SendInput 原樣補送 (injected 旗標，hook 放行)；逾時也補送，鍵不會不見。

兩者都要在同一個執行緒建立，而且那個執行緒要定期呼叫 pump() 跑訊息迴圈。
"""
import ctypes
import ctypes.wintypes as w
import time
from dataclasses import dataclass
from typing import Callable, Optional

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
hid = ctypes.windll.hid

WM_INPUT = 0x00FF
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001
ERROR_CLASS_ALREADY_EXISTS = 1410
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDI_PREPARSEDDATA = 0x20000005
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD, RIM_TYPEHID = 0, 1, 2
HWND_MESSAGE = -3
PM_REMOVE = 1
HIDP_INPUT = 0
HIDP_STATUS_SUCCESS = 0x00110000
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_INJECTED = 0x10
INJECT_MARK = 0x504C5952      # 'PLYR': 自己用 SendInput 補送的鍵，dwExtraInfo 帶這個值，hook 一律放行
RI_MOUSE_WHEEL = 0x0400

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", w.USHORT), ("usUsage", w.USHORT), ("dwFlags", w.DWORD), ("hwndTarget", w.HWND)]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", w.HANDLE), ("dwType", w.DWORD)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", w.DWORD), ("dwSize", w.DWORD), ("hDevice", w.HANDLE), ("wParam", w.WPARAM)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", w.USHORT), ("_pad", w.USHORT), ("usButtonFlags", w.USHORT), ("usButtonData", ctypes.c_short),
                ("ulRawButtons", w.ULONG), ("lLastX", w.LONG), ("lLastY", w.LONG), ("ulExtraInformation", w.ULONG)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", w.USHORT), ("Flags", w.USHORT), ("Reserved", w.USHORT), ("VKey", w.USHORT),
                ("Message", w.UINT), ("ExtraInformation", w.ULONG)]


class RAWHID(ctypes.Structure):
    _fields_ = [("dwSizeHid", w.DWORD), ("dwCount", w.DWORD), ("bRawData", ctypes.c_ubyte * 1)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", w.USHORT), ("UsagePage", w.USHORT), ("InputReportByteLength", w.USHORT),
                ("OutputReportByteLength", w.USHORT), ("FeatureReportByteLength", w.USHORT),
                ("Reserved", w.USHORT * 17), ("NumberLinkCollectionNodes", w.USHORT),
                ("NumberInputButtonCaps", w.USHORT), ("NumberInputValueCaps", w.USHORT),
                ("NumberInputDataIndices", w.USHORT), ("NumberOutputButtonCaps", w.USHORT),
                ("NumberOutputValueCaps", w.USHORT), ("NumberOutputDataIndices", w.USHORT),
                ("NumberFeatureButtonCaps", w.USHORT), ("NumberFeatureValueCaps", w.USHORT),
                ("NumberFeatureDataIndices", w.USHORT)]


class _RANGE(ctypes.Structure):
    _fields_ = [("UsageMin", w.USHORT), ("UsageMax", w.USHORT), ("StringMin", w.USHORT), ("StringMax", w.USHORT),
                ("DesignatorMin", w.USHORT), ("DesignatorMax", w.USHORT), ("DataIndexMin", w.USHORT),
                ("DataIndexMax", w.USHORT)]


class HIDP_BUTTON_CAPS(ctypes.Structure):
    _fields_ = [("UsagePage", w.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", ctypes.c_ubyte),
                ("BitField", w.USHORT), ("LinkCollection", w.USHORT), ("LinkUsage", w.USHORT),
                ("LinkUsagePage", w.USHORT), ("IsRange", ctypes.c_ubyte), ("IsStringRange", ctypes.c_ubyte),
                ("IsDesignatorRange", ctypes.c_ubyte), ("IsAbsolute", ctypes.c_ubyte), ("ReportCount", w.USHORT),
                ("Reserved2", w.USHORT), ("Reserved", ctypes.c_ulong * 9), ("Range", _RANGE)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [("UsagePage", w.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", ctypes.c_ubyte),
                ("BitField", w.USHORT), ("LinkCollection", w.USHORT), ("LinkUsage", w.USHORT),
                ("LinkUsagePage", w.USHORT), ("IsRange", ctypes.c_ubyte), ("IsStringRange", ctypes.c_ubyte),
                ("IsDesignatorRange", ctypes.c_ubyte), ("IsAbsolute", ctypes.c_ubyte), ("HasNull", ctypes.c_ubyte),
                ("Reserved", ctypes.c_ubyte), ("BitSize", w.USHORT), ("ReportCount", w.USHORT),
                ("Reserved2", w.USHORT * 5), ("UnitsExp", ctypes.c_ulong), ("Units", ctypes.c_ulong),
                ("LogicalMin", ctypes.c_long), ("LogicalMax", ctypes.c_long), ("PhysicalMin", ctypes.c_long),
                ("PhysicalMax", ctypes.c_long), ("Range", _RANGE)]


assert ctypes.sizeof(HIDP_BUTTON_CAPS) == 72 and ctypes.sizeof(HIDP_VALUE_CAPS) == 72

k32.GetModuleHandleW.argtypes = [w.LPCWSTR]
k32.GetModuleHandleW.restype = w.HMODULE
u32.GetRawInputDeviceInfoW.argtypes = [w.HANDLE, w.UINT, ctypes.c_void_p, ctypes.POINTER(w.UINT)]
u32.GetRawInputDeviceInfoW.restype = w.UINT
u32.GetRawInputData.argtypes = [w.HANDLE, w.UINT, ctypes.c_void_p, ctypes.POINTER(w.UINT), w.UINT]
u32.GetRawInputData.restype = w.UINT
u32.GetRawInputDeviceList.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.UINT), w.UINT]
u32.GetRawInputDeviceList.restype = w.UINT
u32.RegisterRawInputDevices.argtypes = [ctypes.c_void_p, w.UINT, w.UINT]
u32.RegisterRawInputDevices.restype = w.BOOL
u32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
u32.DefWindowProcW.restype = LRESULT
u32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
u32.CreateWindowExW.restype = w.HWND
u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
u32.DestroyWindow.argtypes = [w.HWND]
u32.SetClassLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_void_p]
u32.SetClassLongPtrW.restype = ctypes.c_void_p
u32.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
u32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
u32.DispatchMessageW.restype = LRESULT
u32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD]
u32.SetWindowsHookExW.restype = w.HHOOK
u32.CallNextHookEx.argtypes = [w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM]
u32.CallNextHookEx.restype = LRESULT
u32.UnhookWindowsHookEx.argtypes = [w.HHOOK]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetButtonCaps.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(w.USHORT), ctypes.c_void_p]
hid.HidP_GetButtonCaps.restype = ctypes.c_long
hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(w.USHORT), ctypes.c_void_p]
hid.HidP_GetValueCaps.restype = ctypes.c_long
hid.HidP_GetUsages.argtypes = [ctypes.c_int, w.USHORT, w.USHORT, ctypes.POINTER(w.USHORT), ctypes.POINTER(w.ULONG),
                               ctypes.c_void_p, ctypes.c_char_p, w.ULONG]
hid.HidP_GetUsages.restype = ctypes.c_long
hid.HidP_MaxUsageListLength.argtypes = [ctypes.c_int, w.USHORT, ctypes.c_void_p]
hid.HidP_MaxUsageListLength.restype = w.ULONG
hid.HidP_GetUsageValue.argtypes = [ctypes.c_int, w.USHORT, w.USHORT, w.USHORT, ctypes.POINTER(w.ULONG),
                                   ctypes.c_void_p, ctypes.c_char_p, w.ULONG]
hid.HidP_GetUsageValue.restype = ctypes.c_long

TYPE_NAME = {RIM_TYPEMOUSE: "mouse", RIM_TYPEKEYBOARD: "keyboard", RIM_TYPEHID: "hid"}
USAGE_PAGE_NAME = {0x01: "GenericDesktop", 0x07: "Keyboard", 0x08: "LED", 0x09: "Button", 0x0C: "Consumer"}
CONSUMER_NAMES = {
    0x30: "Power", 0x40: "Menu", 0x41: "MenuPick", 0x42: "MenuUp", 0x43: "MenuDown", 0x44: "MenuLeft", 0x45: "MenuRight",
    0x46: "MenuEscape", 0x9C: "ChannelUp", 0x9D: "ChannelDown", 0xB0: "Play", 0xB1: "Pause", 0xB2: "Record",
    0xB3: "FastForward", 0xB4: "Rewind", 0xB5: "NextTrack", 0xB6: "PrevTrack", 0xB7: "Stop", 0xB8: "Eject",
    0xCD: "PlayPause", 0xCF: "VoiceCommand", 0xE2: "Mute", 0xE9: "VolumeUp", 0xEA: "VolumeDown",
    0x183: "MediaSelect", 0x184: "WordProcessor", 0x18A: "Email", 0x192: "Calculator", 0x194: "MyComputer",
    0x221: "Search", 0x223: "Home", 0x224: "Back", 0x225: "Forward", 0x226: "Stop", 0x227: "Refresh",
    0x22A: "Bookmarks",
}

# PlayRemote 的實體鍵 → 設定檔裡用的名字 (NOTES.md 對照表)
KEY_NAMES_BY_SCAN = {              # (MakeCode, E0)
    (0x48, True): "up", (0x50, True): "down", (0x4B, True): "left", (0x4D, True): "right",
    (0x02, False): "1", (0x03, False): "2", (0x04, False): "3", (0x05, False): "4", (0x06, False): "5",
    (0x07, False): "6", (0x08, False): "7", (0x09, False): "8", (0x0A, False): "9", (0x0B, False): "0",
    (0x0E, False): "backspace", (0x1C, False): "enter", (0x01, False): "esc",
}
KEY_NAMES_BY_CONSUMER = {0x41: "ok", 0x224: "back", 0x223: "home", 0x40: "menu", 0x30: "power",
                         0xE9: "volume_up", 0xEA: "volume_down", 0xE2: "mute", 0xCD: "play_pause"}
KEY_NAME_TO_VK = {"up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "backspace": 0x08, "enter": 0x0D, "esc": 0x1B,
                  **{str(d): 0x30 + d for d in range(10)}}
# Consumer 鍵被 Windows 翻譯成的虛擬鍵 (要擋的時候用)。實測: ok → Enter。其餘是常見對應，未逐一驗證。
CONSUMER_TO_VK = {0x41: 0x0D, 0x224: 0xA6, 0x223: 0xAC, 0x40: 0x5D, 0xE9: 0xAF, 0xEA: 0xAE, 0xE2: 0xAD, 0xCD: 0xB3}
KEY_NAME_TO_VK.update({name: CONSUMER_TO_VK[u] for u, name in KEY_NAMES_BY_CONSUMER.items() if u in CONSUMER_TO_VK})


@dataclass
class RemoteEvent:
    kind: str            # "key" (鍵盤或 Consumer 按鍵) / "wheel" / "move" / "hid"
    name: str            # 鍵名 (up, ok, 1, ...)，wheel: "wheel_up"/"wheel_down"，未知鍵: "scanXX" / "consumerXXX"
    down: bool           # 按下 True / 放開 False (wheel/move 固定 True)
    vkey: int = 0        # 可用來擋鍵的虛擬鍵碼 (0 = 不知道)
    detail: str = ""     # 原始資訊，給 debug 印
    from_remote: bool = True   # False = 其他鍵盤的事件 (只在 VK 落在 foreign_vks 時才回報，給 KeyBlocker 補送用)


def _device_name(hdev) -> str:
    size = w.UINT(0)
    u32.GetRawInputDeviceInfoW(hdev, RIDI_DEVICENAME, None, ctypes.byref(size))
    buf = ctypes.create_unicode_buffer(size.value + 1)
    u32.GetRawInputDeviceInfoW(hdev, RIDI_DEVICENAME, buf, ctypes.byref(size))
    return buf.value


def _preparsed(hdev):
    size = w.UINT(0)
    u32.GetRawInputDeviceInfoW(hdev, RIDI_PREPARSEDDATA, None, ctypes.byref(size))
    if size.value == 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if u32.GetRawInputDeviceInfoW(hdev, RIDI_PREPARSEDDATA, buf, ctypes.byref(size)) == 0xFFFFFFFF:
        return None
    return buf


def describe_caps(pp) -> list[str]:
    """用 preparsed data 印出這個 collection 的報告格式 (report ID、usage 範圍、數值位元數)。"""
    caps = HIDP_CAPS()
    if hid.HidP_GetCaps(pp, ctypes.byref(caps)) & 0xFFFFFFFF != HIDP_STATUS_SUCCESS:
        return ["    (HidP_GetCaps 失敗)"]
    lines = [f"    top-level usage page 0x{caps.UsagePage:02X} usage 0x{caps.Usage:02X}; "
             f"report bytes in={caps.InputReportByteLength} out={caps.OutputReportByteLength} "
             f"feature={caps.FeatureReportByteLength}"]
    kinds = ((0, "in", caps.NumberInputButtonCaps, caps.NumberInputValueCaps),
             (1, "out", caps.NumberOutputButtonCaps, caps.NumberOutputValueCaps),
             (2, "feature", caps.NumberFeatureButtonCaps, caps.NumberFeatureValueCaps))
    for rtype, kind, nb, nv in kinds:
        if nb:
            arr = (HIDP_BUTTON_CAPS * nb)()
            n = w.USHORT(nb)
            hid.HidP_GetButtonCaps(rtype, arr, ctypes.byref(n), pp)
            for b in arr[:n.value]:
                rng = (f"0x{b.Range.UsageMin:03X}..0x{b.Range.UsageMax:03X}" if b.IsRange
                       else f"0x{b.Range.UsageMin:03X}")
                lines.append(f"    {kind:<7} buttons rid={b.ReportID} page={USAGE_PAGE_NAME.get(b.UsagePage, hex(b.UsagePage))} "
                             f"usage {rng} count={b.ReportCount}")
        if nv:
            arr = (HIDP_VALUE_CAPS * nv)()
            n = w.USHORT(nv)
            hid.HidP_GetValueCaps(rtype, arr, ctypes.byref(n), pp)
            for v in arr[:n.value]:
                rng = (f"0x{v.Range.UsageMin:03X}..0x{v.Range.UsageMax:03X}" if v.IsRange
                       else f"0x{v.Range.UsageMin:03X}")
                lines.append(f"    {kind:<7} value   rid={v.ReportID} page={USAGE_PAGE_NAME.get(v.UsagePage, hex(v.UsagePage))} "
                             f"usage {rng} bits={v.BitSize} count={v.ReportCount} "
                             f"logical=[{v.LogicalMin},{v.LogicalMax}] {'abs' if v.IsAbsolute else 'rel'}")
    return lines


def decode_hid_report(pp, report: bytes):
    """回傳 (usage 清單 [(page, usage)], 軸數值 dict, 文字描述)。"""
    usages, parts = [], []
    for page in (0x0C, 0x01, 0x09, 0x07):
        maxlen = hid.HidP_MaxUsageListLength(HIDP_INPUT, page, pp)
        if not maxlen:
            continue
        arr = (w.USHORT * maxlen)()
        n = w.ULONG(maxlen)
        st = hid.HidP_GetUsages(HIDP_INPUT, page, 0, arr, ctypes.byref(n), pp, report, len(report)) & 0xFFFFFFFF
        if st == HIDP_STATUS_SUCCESS and n.value:
            names = []
            for uval in arr[:n.value]:
                usages.append((page, uval))
                label = CONSUMER_NAMES.get(uval) if page == 0x0C else None
                names.append(f"0x{uval:03X}" + (f"({label})" if label else ""))
            parts.append(f"{USAGE_PAGE_NAME.get(page, hex(page))}: {' '.join(names)}")
    axes = {}
    for usage, label in ((0x30, "X"), (0x31, "Y"), (0x32, "Z"), (0x33, "Rx"), (0x34, "Ry"), (0x35, "Rz"), (0x39, "Hat")):
        val = w.ULONG(0)
        st = hid.HidP_GetUsageValue(HIDP_INPUT, 0x01, 0, usage, ctypes.byref(val), pp, report, len(report)) & 0xFFFFFFFF
        if st == HIDP_STATUS_SUCCESS:
            axes[label] = val.value
    if axes:
        parts.append("axes: " + " ".join(f"{k}={v}" for k, v in axes.items()))
    return usages, axes, ("; ".join(parts) if parts else "(no usages)")


class RawInputListener:
    """收 Raw Input，只把 device name 含 vid_tag 的事件交給 on_event。"""

    TARGETS = [(0x01, 0x06), (0x01, 0x02), (0x0C, 0x01), (0x01, 0x05), (0x01, 0x04), (0x01, 0x80)]

    def __init__(self, on_event: Callable[[RemoteEvent], None], vid_tag: str = "VID&012D01",
                 on_raw: Optional[Callable[[str, str], None]] = None):
        self.on_event = on_event
        self.on_raw = on_raw                 # (label, text) 原始事件，給 hidwatch 印
        self.vid_tag = vid_tag.upper()
        self.devices: dict[int, tuple[str, str]] = {}
        self.foreign: set[int] = set()        # 已確認不是遙控器的裝置 handle
        self.foreign_vks: set[int] = set()    # 其他鍵盤按到這些 VK 時也要回報 (KeyBlocker 要補送)
        self.pp_cache: dict = {}
        self.consumer_down: dict[int, set] = {}   # hDevice → 目前按住的 consumer usage
        self._wndproc = WNDPROC(self._wnd_proc)
        self.hwnd = None

    # ---- 裝置列舉 ----
    @staticmethod
    def _label(name: str, dwtype: int) -> str:
        up = name.upper()
        col = up.split("&COL")[1][:2] if "&COL" in up else "??"
        return f"col{col}/{TYPE_NAME.get(dwtype, dwtype)}"

    def list_devices(self) -> dict:
        n = w.UINT(0)
        u32.GetRawInputDeviceList(None, ctypes.byref(n), ctypes.sizeof(RAWINPUTDEVICELIST))
        arr = (RAWINPUTDEVICELIST * n.value)()
        u32.GetRawInputDeviceList(arr, ctypes.byref(n), ctypes.sizeof(RAWINPUTDEVICELIST))
        out = {}
        for d in arr[:n.value]:
            name = _device_name(d.hDevice)
            if self.vid_tag in name.upper():
                out[d.hDevice] = (self._label(name, d.dwType), name)
        self.devices = dict(sorted(out.items(), key=lambda kv: kv[1][0]))
        return self.devices

    def preparsed(self, hdev):
        if hdev not in self.pp_cache:
            self.pp_cache[hdev] = _preparsed(hdev)
        return self.pp_cache[hdev]

    # ---- 啟動 ----
    def start(self):
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.lpszClassName = "PlayRemoteRawInput"
        wc.hInstance = k32.GetModuleHandleW(None)
        if not u32.RegisterClassW(ctypes.byref(wc)):
            if ctypes.GetLastError() != ERROR_CLASS_ALREADY_EXISTS:      # 同一程式內重啟驅動: 類別已在，沿用
                raise OSError(f"RegisterClass 失敗: {ctypes.GetLastError()}")
            u32.SetClassLongPtrW(u32.CreateWindowExW(0, wc.lpszClassName, "tmp", 0, 0, 0, 0, 0, HWND_MESSAGE, None,
                                                     wc.hInstance, None), -24, ctypes.cast(self._wndproc, ctypes.c_void_p).value)
        self.hwnd = u32.CreateWindowExW(0, wc.lpszClassName, "playremote", 0, 0, 0, 0, 0, HWND_MESSAGE,
                                        None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError(f"CreateWindow 失敗: {ctypes.GetLastError()}")
        rids = (RAWINPUTDEVICE * len(self.TARGETS))()
        for r, (page, usage) in zip(rids, self.TARGETS):
            r.usUsagePage, r.usUsage, r.dwFlags, r.hwndTarget = page, usage, RIDEV_INPUTSINK, self.hwnd
        if not u32.RegisterRawInputDevices(rids, len(self.TARGETS), ctypes.sizeof(RAWINPUTDEVICE)):
            raise OSError(f"RegisterRawInputDevices 失敗: {ctypes.GetLastError()}")
        self.list_devices()

    def stop(self):
        """取消 Raw Input 註冊並銷毀訊息視窗（重啟驅動前呼叫）。"""
        if self.hwnd:
            rids = (RAWINPUTDEVICE * len(self.TARGETS))()
            for r, (page, usage) in zip(rids, self.TARGETS):
                r.usUsagePage, r.usUsage, r.dwFlags, r.hwndTarget = page, usage, RIDEV_REMOVE, None
            u32.RegisterRawInputDevices(rids, len(self.TARGETS), ctypes.sizeof(RAWINPUTDEVICE))
            u32.DestroyWindow(self.hwnd)
            self.hwnd = None

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            try:
                self._on_input(lparam)
            except Exception as e:  # noqa: BLE001
                print("rawinput decode error:", e)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _foreign_key(self, buf, off):
        """其他鍵盤的按鍵: 只有 VK 在 foreign_vks 裡才回報 (讓 KeyBlocker 把先擋下的鍵補送回去)。"""
        kb = RAWKEYBOARD.from_buffer(buf, off)
        if kb.VKey in self.foreign_vks:
            down = not (kb.Flags & 1)
            self.on_event(RemoteEvent("key", f"vk{kb.VKey:02X}", down, kb.VKey,
                                      f"foreign scan=0x{kb.MakeCode:02X} vkey=0x{kb.VKey:02X}", from_remote=False))

    def _on_input(self, lparam):
        size = w.UINT(0)
        u32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
        buf = ctypes.create_string_buffer(size.value)
        if u32.GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER)) == 0xFFFFFFFF:
            return
        hdr = RAWINPUTHEADER.from_buffer(buf)
        off = ctypes.sizeof(RAWINPUTHEADER)
        if hdr.hDevice not in self.devices:
            if hdr.hDevice in self.foreign or hdr.dwType != RIM_TYPEKEYBOARD:
                if hdr.dwType == RIM_TYPEKEYBOARD:
                    self._foreign_key(buf, off)
                return
            name = _device_name(hdr.hDevice) if hdr.hDevice else ""
            if self.vid_tag not in name.upper():
                self.foreign.add(hdr.hDevice)
                self._foreign_key(buf, off)
                return
            self.devices[hdr.hDevice] = (self._label(name, hdr.dwType), name)
        label = self.devices[hdr.hDevice][0]
        if hdr.dwType == RIM_TYPEKEYBOARD:
            kb = RAWKEYBOARD.from_buffer(buf, off)
            down = not (kb.Flags & 1)
            e0 = bool(kb.Flags & 2)
            name = KEY_NAMES_BY_SCAN.get((kb.MakeCode, e0), f"scan{kb.MakeCode:02X}{'e0' if e0 else ''}")
            detail = f"{'down' if down else 'up  '} scan=0x{kb.MakeCode:02X}{' E0' if e0 else ''} vkey=0x{kb.VKey:02X} msg=0x{kb.Message:03X}"
            if self.on_raw:
                self.on_raw(label, detail)
            self.on_event(RemoteEvent("key", name, down, kb.VKey, detail))
        elif hdr.dwType == RIM_TYPEMOUSE:
            m = RAWMOUSE.from_buffer(buf, off)
            detail = f"flags=0x{m.usFlags:02X} btnFlags=0x{m.usButtonFlags:04X} btnData={m.usButtonData} dx={m.lLastX} dy={m.lLastY}"
            if self.on_raw:
                self.on_raw(label, detail)
            if m.usButtonFlags & RI_MOUSE_WHEEL:
                self.on_event(RemoteEvent("wheel", "wheel_up" if m.usButtonData > 0 else "wheel_down", True, 0, detail))
            elif m.lLastX or m.lLastY:
                self.on_event(RemoteEvent("move", "move", True, 0, detail))
        else:
            rh = RAWHID.from_buffer(buf, off)
            data_off = off + 8
            pp = self.preparsed(hdr.hDevice)
            for i in range(rh.dwCount):
                rep = bytes(buf[data_off + i * rh.dwSizeHid: data_off + (i + 1) * rh.dwSizeHid])
                if pp is None:
                    if self.on_raw:
                        self.on_raw(label, f"report={rep.hex(' ')} (no preparsed data)")
                    continue
                usages, _axes, text = decode_hid_report(pp, rep)
                if self.on_raw:
                    self.on_raw(label, f"report={rep.hex(' ')}  {text}")
                now_down = {u for (page, u) in usages if page == 0x0C}
                was_down = self.consumer_down.get(hdr.hDevice, set())
                for u in now_down - was_down:
                    self.on_event(RemoteEvent("key", KEY_NAMES_BY_CONSUMER.get(u, f"consumer{u:03X}"), True,
                                              CONSUMER_TO_VK.get(u, 0), f"consumer 0x{u:03X} down"))
                for u in was_down - now_down:
                    self.on_event(RemoteEvent("key", KEY_NAMES_BY_CONSUMER.get(u, f"consumer{u:03X}"), False,
                                              CONSUMER_TO_VK.get(u, 0), f"consumer 0x{u:03X} up"))
                self.consumer_down[hdr.hDevice] = now_down
                other = [(p, u) for (p, u) in usages if p != 0x0C]
                if other:
                    self.on_event(RemoteEvent("hid", "hid", True, 0, text))


class KeyBlocker:
    """低階鍵盤 hook。queued: 先擋後補的 VK (Consumer 鍵)；其餘靠 expect()/held (鍵盤 collection 的鍵)。"""

    def __init__(self, queued: Optional[set] = None, max_age: float = 0.05, window: float = 0.25,
                 on_block: Optional[Callable[[int, bool], None]] = None):
        self.queued: set[int] = set(queued or ())
        self.max_age = max_age
        self.window = window
        self.on_block = on_block
        self.queue: list[tuple[int, bool, int, int, float]] = []   # (vk, down, scan, flags, t)
        self.pre_consume: dict[tuple[int, bool], float] = {}   # Raw Input 比 hook 先到: 之後 hook 看到就直接丟
        self.pre_pass: dict[tuple[int, bool], float] = {}      # Raw Input 比 hook 先到 (實體鍵盤): 之後 hook 放行
        self.pending: dict[tuple[int, bool], float] = {}
        self.held: set[int] = set()
        self.blocked = 0
        self.replayed = 0
        self._proc = HOOKPROC(self._hook)
        self.hhook = None

    def start(self):
        self.hhook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, k32.GetModuleHandleW(None), 0)
        if not self.hhook:
            raise OSError(f"SetWindowsHookEx 失敗: {ctypes.GetLastError()}")

    def stop(self):
        if self.hhook:
            u32.UnhookWindowsHookEx(self.hhook)
            self.hhook = None
        self.flush(force=True)

    # ---- 鍵盤 collection 的鍵 (Raw Input 事後登記) ----
    def expect(self, vk: int, down: bool):
        if not vk:
            return
        self.pending[(vk, down)] = time.monotonic()
        if down:
            self.held.add(vk)
        else:
            self.held.discard(vk)

    # ---- Consumer 鍵 (先擋後補) ----
    def consume(self, vk: int, down: bool):
        if self._pop(vk, down) is None:
            self.pre_consume[(vk, down)] = time.monotonic()

    def replay(self, vk: int, down: bool):
        item = self._pop(vk, down)
        if item:
            self._inject(item)
        else:
            self.pre_pass[(vk, down)] = time.monotonic()

    def flush(self, force: bool = False):
        now = time.monotonic()
        keep = []
        for item in self.queue:
            if force or now - item[4] > self.max_age:
                self._inject(item)
            else:
                keep.append(item)
        self.queue = keep

    def _pop(self, vk: int, down: bool):
        for i, item in enumerate(self.queue):
            if item[0] == vk and item[1] == down:
                return self.queue.pop(i)
        return None

    def _inject(self, item):
        vk, down, scan, flags, _t = item
        import winio
        inp = winio.INPUT(type=winio.INPUT_KEYBOARD)
        inp.u.ki.wVk = vk
        inp.u.ki.wScan = scan
        inp.u.ki.dwFlags = (winio.KEYEVENTF_KEYUP if not down else 0) | (winio.KEYEVENTF_EXTENDEDKEY if flags & 1 else 0)
        inp.u.ki.dwExtraInfo = INJECT_MARK
        winio.u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(winio.INPUT))
        self.replayed += 1

    def _hook(self, code, wparam, lparam):
        if code == 0:
            kb = KBDLLHOOKSTRUCT.from_address(lparam)
            if kb.dwExtraInfo == INJECT_MARK:                       # 自己補送的，放行
                return u32.CallNextHookEx(self.hhook, code, wparam, lparam)
            down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            vk = kb.vkCode
            now = time.monotonic()
            block = False
            if vk in self.queued:
                # Consumer 鍵翻譯出來的 VK 帶 injected 旗標，所以這裡不看 injected，只認自己的標記
                key = (vk, down)
                t = self.pre_consume.pop(key, None)
                if t is not None and now - t < self.window:
                    block = True                                    # 遙控器的，Raw Input 已先到: 丟掉
                elif self.pre_pass.pop(key, None) is not None:
                    block = False                                   # 實體鍵盤的，Raw Input 已先到: 放行
                else:
                    self.queue.append((vk, down, kb.scanCode, kb.flags, now))
                    block = True                                    # 先擋，等 Raw Input 決定
            elif not (kb.flags & LLKHF_INJECTED):
                t = self.pending.pop((vk, down), None)
                if (t is not None and now - t < self.window) or (down and vk in self.held):
                    block = True
            if block:
                self.blocked += 1
                if self.on_block:
                    self.on_block(vk, down)
                return 1
        return u32.CallNextHookEx(self.hhook, code, wparam, lparam)


_msg = w.MSG()


def pump(only_input: bool = False):
    """處理目前執行緒排隊中的視窗訊息。only_input=True 只處理 WM_INPUT (給 hook 內用)。"""
    lo, hi = (WM_INPUT, WM_INPUT) if only_input else (0, 0)
    while u32.PeekMessageW(ctypes.byref(_msg), None, lo, hi, PM_REMOVE):
        u32.TranslateMessage(ctypes.byref(_msg))
        u32.DispatchMessageW(ctypes.byref(_msg))
