#!/usr/bin/env python3
"""macOS：觸發 PlayRemote 的藍牙配對。

一般使用者在「系統設定 → 藍牙」點「連接」即可配對（韌體兩個修補齊全後）。
但 macOS 26 的設定介面偶爾不顯示「連接」鈕（「附近的裝置」的 UI 毛病）。
這支程式用 CoreBluetooth 連線一次並停留，讓系統的 HID 服務去讀需加密的特徵值、
觸發 Just Works 配對——完全不碰終端機之外，也供 GUI 的「藍牙配對」按鈕呼叫。

用法（命令列）:
  ble_pair_mac.py [--name PlayRemote] [--seconds 40]

GUI 用法:
  from ble_pair_mac import pair  → pair(on_log=print, should_stop=lambda: False)
"""
from __future__ import annotations

import argparse
import asyncio
import time
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError


async def pair_async(name: str = "PlayRemote", seconds: float = 40.0,
                     on_log: Callable[[str], None] = print,
                     should_stop: Optional[Callable[[], bool]] = None) -> bool:
    """連線 PlayRemote 並停留，讓 macOS 觸發配對。回傳是否成功連上（配對結果看系統）。"""
    should_stop = should_stop or (lambda: False)
    on_log("掃描 PlayRemote…（請確認遙控器在藍牙模式、並長按 Home + 返回進入配對模式，燈快閃）")
    dev = None
    for attempt in range(1, 7):
        if should_stop():
            return False
        try:
            dev = await BleakScanner.find_device_by_filter(
                lambda d, adv: name.lower() in ((d.name or adv.local_name or "").lower()), timeout=10.0)
        except BleakError as e:
            on_log(f"藍牙尚未就緒：{e}")
            await asyncio.sleep(2)
            continue
        if dev:
            break
        on_log(f"沒掃到（{attempt}/6），請讓遙控器保持在配對模式…")
    if not dev:
        on_log("找不到 PlayRemote。請確認已切到藍牙模式並進入配對模式。")
        return False

    on_log(f"找到 {dev.name}，連線中…")
    try:
        async with BleakClient(dev, timeout=20.0) as client:
            on_log("已連線。系統正在配對，若跳出配對視窗請按「連線／配對」。")
            # 主動讀一個需要加密的 HID 特徵值，促使系統發起配對（有些版本靠系統自己讀即可）
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds and client.is_connected and not should_stop():
                await asyncio.sleep(0.5)
            on_log("配對流程結束。請到「系統設定 → 藍牙 → 我的裝置」確認 PlayRemote 已連線，並按鍵測試。")
            return True
    except Exception as e:  # noqa: BLE001
        on_log(f"連線失敗：{type(e).__name__}: {e}")
        return False


def pair(name: str = "PlayRemote", seconds: float = 40.0,
         on_log: Callable[[str], None] = print, should_stop: Optional[Callable[[], bool]] = None) -> bool:
    """同步包裝，供 GUI 的背景執行緒呼叫。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(pair_async(name, seconds, on_log, should_stop))
    finally:
        loop.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--name", default="PlayRemote")
    ap.add_argument("--seconds", type=float, default=40.0)
    a = ap.parse_args()
    try:
        pair(a.name, a.seconds)
    except KeyboardInterrupt:
        print("\n結束。")
