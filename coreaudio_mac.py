"""macOS CoreAudio（ctypes）：列出輸入裝置、讀取 / 設定系統預設麥克風。不需要額外安裝。"""
from __future__ import annotations

import ctypes
import ctypes.util
from typing import Optional

_ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))
_cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


kAudioObjectSystemObject = 1
kAudioObjectPropertyScopeGlobal = _fourcc("glob")
kAudioObjectPropertyScopeInput = _fourcc("inpt")
kAudioObjectPropertyElementMain = 0
kAudioHardwarePropertyDevices = _fourcc("dev#")
kAudioHardwarePropertyDefaultInputDevice = _fourcc("dIn ")
kAudioObjectPropertyName = _fourcc("lnam")
kAudioDevicePropertyStreams = _fourcc("stm#")


class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32), ("mElement", ctypes.c_uint32)]


_ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
_ca.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(AudioObjectPropertyAddress),
                                               ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
_ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
_ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(AudioObjectPropertyAddress),
                                           ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
_ca.AudioObjectSetPropertyData.restype = ctypes.c_int32
_ca.AudioObjectSetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(AudioObjectPropertyAddress),
                                           ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
_cf.CFStringGetCString.restype = ctypes.c_bool
_cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
_cf.CFRelease.argtypes = [ctypes.c_void_p]


def _addr(selector: int, scope: int = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress:
    return AudioObjectPropertyAddress(selector, scope, kAudioObjectPropertyElementMain)


def _get(obj: int, addr: AudioObjectPropertyAddress, ctype):
    size = ctypes.c_uint32(ctypes.sizeof(ctype))
    out = ctype()
    err = _ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(out))
    if err:
        raise OSError(f"AudioObjectGetPropertyData 失敗 ({err})")
    return out


def device_name(dev: int) -> str:
    ref = _get(dev, _addr(kAudioObjectPropertyName), ctypes.c_void_p)
    if not ref.value:
        return ""
    buf = ctypes.create_string_buffer(256)
    _cf.CFStringGetCString(ref.value, buf, 256, 0x08000100)
    _cf.CFRelease(ref.value)
    return buf.value.decode("utf-8", "replace")


def has_input(dev: int) -> bool:
    addr = _addr(kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput)
    size = ctypes.c_uint32(0)
    if _ca.AudioObjectGetPropertyDataSize(dev, ctypes.byref(addr), 0, None, ctypes.byref(size)):
        return False
    return size.value > 0


def list_input_devices() -> list[tuple[int, str]]:
    addr = _addr(kAudioHardwarePropertyDevices)
    size = ctypes.c_uint32(0)
    if _ca.AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, ctypes.byref(addr), 0, None, ctypes.byref(size)):
        return []
    n = size.value // 4
    arr = (ctypes.c_uint32 * n)()
    if _ca.AudioObjectGetPropertyData(kAudioObjectSystemObject, ctypes.byref(addr), 0, None, ctypes.byref(size), arr):
        return []
    return [(d, device_name(d)) for d in arr if has_input(d)]


def get_default_input() -> int:
    return _get(kAudioObjectSystemObject, _addr(kAudioHardwarePropertyDefaultInputDevice), ctypes.c_uint32).value


def set_default_input(dev: int):
    addr = _addr(kAudioHardwarePropertyDefaultInputDevice)
    val = ctypes.c_uint32(dev)
    err = _ca.AudioObjectSetPropertyData(kAudioObjectSystemObject, ctypes.byref(addr), 0, None, 4, ctypes.byref(val))
    if err:
        raise OSError(f"設定預設麥克風失敗 ({err})")


def find_input(name_hint: str) -> Optional[int]:
    for dev, name in list_input_devices():
        if name_hint.lower() in name.lower():
            return dev
    return None


class MicSwitcher:
    """語音鍵按下時把系統預設麥克風切成指定裝置，放開後切回原本的。"""

    def __init__(self, name_hint: str = "PlayRemote"):
        self.name_hint = name_hint
        self._saved: Optional[int] = None

    @property
    def available(self) -> bool:
        return find_input(self.name_hint) is not None

    def switch(self) -> bool:
        target = find_input(self.name_hint)
        if target is None:
            return False
        cur = get_default_input()
        if cur == target:
            self._saved = None
            return True
        self._saved = cur
        set_default_input(target)
        return True

    def restore(self):
        if self._saved is not None:
            try:
                set_default_input(self._saved)
            finally:
                self._saved = None


if __name__ == "__main__":
    cur = get_default_input()
    for d, n in list_input_devices():
        print(f"{'*' if d == cur else ' '} [{d}] {n}")
