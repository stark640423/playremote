"""Windows 輸出端：送組合鍵、打字到目前視窗、啟動程式。只用 ctypes。"""
import ctypes
import ctypes.wintypes as w
import subprocess

u32 = ctypes.windll.user32

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", w.DWORD), ("u", _INPUTUNION)]


u32.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
u32.SendInput.restype = w.UINT
u32.MapVirtualKeyW.argtypes = [w.UINT, w.UINT]
u32.MapVirtualKeyW.restype = w.UINT

VK = {
    "ctrl": 0x11, "control": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "alt": 0x12, "lalt": 0xA4, "ralt": 0xA5,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91, "printscreen": 0x2C, "pause": 0x13,
    "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "play_pause": 0xB3, "next_track": 0xB0, "prev_track": 0xB1,
    "browser_back": 0xA6, "browser_forward": 0xA7, "browser_home": 0xAC,
}
for i in range(1, 25):
    VK[f"f{i}"] = 0x6F + i
for c in "0123456789":
    VK[c] = ord(c)
for c in "abcdefghijklmnopqrstuvwxyz":
    VK[c] = ord(c.upper())
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0xA3, 0xA5,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB3, 0xA6, 0xA7, 0xAC}


def parse_combo(combo: str) -> list[int]:
    """'ctrl+shift+v' → [0x11, 0x10, 0x56]。也接受 'vk0x7C' 這種寫法。"""
    keys = []
    for part in combo.replace(" ", "").lower().split("+"):
        if not part:
            continue
        if part.startswith("vk0x"):
            keys.append(int(part[2:], 16))
        elif part in VK:
            keys.append(VK[part])
        else:
            raise ValueError(f"不認得的鍵名: {part}")
    return keys


def _key_input(vk: int, up: bool) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = u32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    inp.u.ki.dwFlags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
    return inp


def key_down(vks: list[int]):
    arr = (INPUT * len(vks))(*[_key_input(vk, False) for vk in vks])
    u32.SendInput(len(vks), arr, ctypes.sizeof(INPUT))


def key_up(vks: list[int]):
    arr = (INPUT * len(vks))(*[_key_input(vk, True) for vk in reversed(vks)])
    u32.SendInput(len(vks), arr, ctypes.sizeof(INPUT))


def send_keys(combo: str):
    """按一下組合鍵，例如 'ctrl+shift+v'、'enter'、'win+h'。"""
    vks = parse_combo(combo)
    key_down(vks)
    key_up(vks)


def type_text(text: str):
    """用 Unicode 輸入把文字打進目前焦點的視窗 (中文也行)。"""
    inputs = []
    for ch in text:
        if ch == "\n":
            inputs.append(_key_input(VK["enter"], False))
            inputs.append(_key_input(VK["enter"], True))
            continue
        enc = ch.encode("utf-16-le")
        units = [int.from_bytes(enc[i:i + 2], "little") for i in range(0, len(enc), 2)]
        for unit in units:
            for up in (False, True):
                inp = INPUT(type=INPUT_KEYBOARD)
                inp.u.ki.wScan = unit
                inp.u.ki.dwFlags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
                inputs.append(inp)
    if inputs:
        arr = (INPUT * len(inputs))(*inputs)
        u32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def run(command: str):
    """在背景啟動程式 (經 shell，所以 'start ...'、'code .' 之類都能用)。"""
    subprocess.Popen(command, shell=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
