#!/usr/bin/env python3
"""macOS：監看 PlayRemote USB 接收器送出的 HID 報告（IOHIDManager，ctypes 直接呼叫 IOKit）。

用法:
  hidwatch_mac.py [--vid 0x0610] [--pid 0x1000]

需要「輸入監控」權限：系統設定 → 隱私權與安全性 → 輸入監控 → 允許「終端機」。
按遙控器的每一個鍵，會印出 (usage page/usage/report id) 與原始位元組。Ctrl+C 結束。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import time

iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))

kCFAllocatorDefault = None
kCFStringEncodingUTF8 = 0x08000100
kCFNumberIntType = 9
kIOHIDOptionsTypeNone = 0
kIOHIDReportTypeInput = 0

cf.CFStringCreateWithCString.restype = ctypes.c_void_p
cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
cf.CFNumberCreate.restype = ctypes.c_void_p
cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
cf.CFDictionaryCreateMutable.restype = ctypes.c_void_p
cf.CFDictionaryCreateMutable.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
cf.CFDictionarySetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
cf.CFRunLoopRunInMode.restype = ctypes.c_int32
cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
cf.CFNumberGetValue.restype = ctypes.c_bool
kCFRunLoopDefaultMode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
kCFTypeDictionaryKeyCallBacks = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")
kCFTypeDictionaryValueCallBacks = ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")

iokit.IOHIDManagerCreate.restype = ctypes.c_void_p
iokit.IOHIDManagerCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDManagerSetDeviceMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerRegisterDeviceMatchingCallback.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDManagerOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
iokit.IOHIDManagerOpen.restype = ctypes.c_int
iokit.IOHIDDeviceRegisterInputReportCallback.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
iokit.IOHIDDeviceGetProperty.restype = ctypes.c_void_p
iokit.IOHIDDeviceGetProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

DeviceCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
ReportCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint8), ctypes.c_long)


def cfstr(s: str):
    return cf.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8)


def cfnum(v: int):
    c = ctypes.c_int(v)
    return cf.CFNumberCreate(None, kCFNumberIntType, ctypes.byref(c))


def dev_int_prop(dev, key: str) -> int:
    ref = iokit.IOHIDDeviceGetProperty(dev, cfstr(key))
    if not ref:
        return -1
    out = ctypes.c_int(0)
    cf.CFNumberGetValue(ref, kCFNumberIntType, ctypes.byref(out))
    return out.value


BUFS = []       # 每個裝置的報告緩衝，避免被 GC
CALLBACKS = []  # 保住 callback 物件
START = time.monotonic()
LAST = {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--vid", type=lambda x: int(x, 0), default=0x0610)
    p.add_argument("--pid", type=lambda x: int(x, 0), default=0x1000)
    a = p.parse_args()

    mgr = iokit.IOHIDManagerCreate(None, kIOHIDOptionsTypeNone)
    match = cf.CFDictionaryCreateMutable(None, 0, ctypes.byref(kCFTypeDictionaryKeyCallBacks),
                                         ctypes.byref(kCFTypeDictionaryValueCallBacks))
    cf.CFDictionarySetValue(match, cfstr("VendorID"), cfnum(a.vid))
    cf.CFDictionarySetValue(match, cfstr("ProductID"), cfnum(a.pid))
    iokit.IOHIDManagerSetDeviceMatching(mgr, match)

    prev = {}

    def on_report(ctx, result, sender, rtype, report_id, data, length):
        page, usage = LAST.get(sender, ("?", "?"))
        raw = bytes(data[:length])
        # 接收器每 0.5 秒送一次內容相同的閒置心跳 (id 196)，和上一筆相同就不印
        if prev.get((sender, report_id)) == raw:
            return
        prev[(sender, report_id)] = raw
        t = time.monotonic() - START
        print(f"{t:8.3f}  page=0x{page:02x} usage=0x{usage:02x}  id={report_id:<3} len={length:<2} {raw.hex(' ')}")

    report_cb = ReportCB(on_report)
    CALLBACKS.append(report_cb)

    def on_device(ctx, result, sender, device):
        page = dev_int_prop(device, "PrimaryUsagePage")
        usage = dev_int_prop(device, "PrimaryUsage")
        LAST[device] = (page, usage)
        buf = (ctypes.c_uint8 * 64)()
        BUFS.append(buf)
        iokit.IOHIDDeviceRegisterInputReportCallback(device, buf, 64, report_cb, None)
        print(f"  已掛上 HID 介面: page=0x{page:02x} usage=0x{usage:02x}")

    device_cb = DeviceCB(on_device)
    CALLBACKS.append(device_cb)
    iokit.IOHIDManagerRegisterDeviceMatchingCallback(mgr, device_cb, None)
    iokit.IOHIDManagerScheduleWithRunLoop(mgr, cf.CFRunLoopGetCurrent(), kCFRunLoopDefaultMode)
    ret = iokit.IOHIDManagerOpen(mgr, kIOHIDOptionsTypeNone)
    if ret != 0:
        print(f"IOHIDManagerOpen 失敗 (0x{ret & 0xffffffff:x})。多半是沒有「輸入監控」權限：\n"
              "系統設定 → 隱私權與安全性 → 輸入監控 → 允許「終端機」，然後完全退出終端機再重開。")
        return
    print("監看中。請逐一按每個鍵（數字、方向、OK、返回、Home、彩色鍵、語音鍵、滾輪），每按一個停一下。Ctrl+C 結束。\n")
    try:
        while True:
            cf.CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.25, False)
    except KeyboardInterrupt:
        print("\n結束。")


if __name__ == "__main__":
    main()
