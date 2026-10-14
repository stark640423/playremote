#!/usr/bin/env python3
"""PlayRemote HID 監看工具 (Windows 專用)。

Windows 把 PlayRemote 當成標準 HID 裝置 (鍵盤 / Consumer Control / 滑鼠 / 遊戲控制器)。
這個工具用 Raw Input (WM_INPUT) 接收**只來自 PlayRemote** 的輸入事件並印出原始內容，
用來對照「哪個實體按鍵 → 哪個 HID usage / 掃描碼」。同時寫入 captures/hid-*.log。

用法:
  hidwatch.py            監看 (Ctrl+C 結束)
  hidwatch.py --list     只列出 PlayRemote 的 HID 裝置與各 collection 的報告格式

不需要 pip 套件，只用 ctypes (實作在 rawinput.py)。
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import rawinput

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    start = time.monotonic()
    log = None

    def on_raw(label, text):
        line = f"{time.monotonic() - start:9.3f}  {label:<14} {text}"
        print(line)
        if log:
            log.write(line + "\n")
            log.flush()

    def on_event(ev: rawinput.RemoteEvent):
        if ev.kind == "key":
            print(f"{'':9}  => 鍵名 {ev.name} {'按下' if ev.down else '放開'}")
        elif ev.kind == "wheel":
            print(f"{'':9}  => {ev.name}")

    listener = rawinput.RawInputListener(on_event, on_raw=on_raw)
    devices = listener.list_devices()
    print("Raw Input 看到的 PlayRemote 裝置:")
    for hdev, (label, name) in devices.items():
        print(f"  {label:<14} {name}")
        pp = listener.preparsed(hdev)
        print("\n".join(rawinput.describe_caps(pp)) if pp is not None else "    (無 preparsed data)")
    if not devices:
        print("  (沒有。請確認 PlayRemote 已在 Windows 藍牙設定配對，並按一下遙控器喚醒它。)")
    if args.list:
        return

    outdir = Path(__file__).parent / "captures"
    outdir.mkdir(exist_ok=True)
    logfile = outdir / f"hid-{datetime.now():%Y%m%d-%H%M%S}.log"
    log = logfile.open("w", encoding="utf-8")
    listener.start()
    print(f"\n開始監看，紀錄檔: {logfile}")
    print("請依序: 單獨按每個按鍵 → 轉動遙控器 (空中滑鼠) → 滾輪 → 按住麥克風鍵。"
          "每做一個動作稍停一下，方便對照。Ctrl+C 結束。\n")
    try:
        while True:
            rawinput.pump()
            time.sleep(0.005)
    except KeyboardInterrupt:
        print("\n結束。")
    finally:
        log.close()


if __name__ == "__main__":
    main()
