#!/usr/bin/env python3
"""PlayRemote 韌體修補：讓 macOS / iOS 把它歸類為鍵盤並提供藍牙配對。

用法:
  fwpatch.py 原始.bin 輸出.bin [--appearance 0x03C1]

只針對已分析過的韌體版本（2.4g+ble-pc.bin，2022-08-29，sha256 37cbd95d72fc5f81…），
每個修改都先比對原始位元組，不相符就整個中止，不會產生半套修改的檔案。

修改內容（陣列長度都不變，避免動到程式碼裡寫死的 sizeof）：
1. ADV 主廣播 @0x219c3：把廠商資料 `05 FF 5D 00 55 66`（6 bytes）換成
   Appearance `03 19 C1 03` + `00 00`（AD 規格允許的非有效填充）。
   → 主廣播變成 Flags / UUID16 1812 / Appearance 0x03C1 / 名稱 PlayRemote。
2. 掃描回應 @0x219e0：Appearance 0x0180（Generic Remote Control）改成 0x03C1（Keyboard），與主廣播一致。
--minimal：只做第 2 項（2 bytes）。2026-10-15 對照 Mi Smart Mouse 的日誌後，這是最可能就足夠、也最安全的改法，建議第一輪先用它。
3.（選用 --gatt-appearance）GATT 的 GAP Appearance 值 @0x2121E：0x0180 → 新值。
   位置是推定的：rodata 中緊接在 UUID 0x2A01 常數後面的 u16 0x0180。建議先不加，第一輪只改廣播。
廠商資料 0x005D 仍保留在掃描回應中（若 USB 接收器或其他主機用它辨識，不受影響）。
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

KNOWN_SHA256 = "37cbd95d72fc5f81"   # 前 16 碼


def main():
    # Windows 中文主控台預設 cp950，印不出 ✓ 等字元
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--appearance", type=lambda x: int(x, 0), default=0x03C1,
                    help="0x03C1 Keyboard（預設）/ 0x03C2 Mouse / 0x03C0 Generic HID / 0x0180 原值")
    ap.add_argument("--force", action="store_true", help="版本不同時仍嘗試（仍會逐項比對原始位元組）")
    ap.add_argument("--minimal", action="store_true",
                    help="只改掃描回應的 Appearance 值（2 bytes），主廣播完全不動。依 Mi Smart Mouse 對照：macOS 會讀到掃描回應的 "
                         "appearance，只是 0x0180 不在它認得的類型裡（Aprc ?），改成 0x03C1 可能就足夠")
    ap.add_argument("--gatt-appearance", action="store_true",
                    help="一併修改 GATT 的 GAP Appearance 特徵值 @0x2121E（推定：緊接在 UUID 0x2A01 常數之後的 0x0180）")
    a = ap.parse_args()

    data = bytearray(Path(a.src).read_bytes())
    digest = hashlib.sha256(data).hexdigest()
    if not digest.startswith(KNOWN_SHA256) and not a.force:
        raise SystemExit(f"這不是分析過的韌體版本（sha256 {digest[:16]}…）。確認後可加 --force，仍會逐項比對。")

    app = struct.pack("<H", a.appearance)
    patches = [
        ("ADV：廠商資料 → Appearance", 0x219C3, bytes.fromhex("05ff5d005566"), bytes([0x03, 0x19]) + app + b"\x00\x00"),
        ("掃描回應：Appearance 0x0180 → 新值", 0x219E0, bytes.fromhex("03198001"), bytes([0x03, 0x19]) + app),
    ]
    if a.minimal:
        patches = [p for p in patches if p[1] == 0x219E0]
    if a.gatt_appearance:
        patches.append(("GATT GAP Appearance 值（推定位置）", 0x2121E, bytes.fromhex("8001"), app))
    for name, off, old, new in patches:
        cur = bytes(data[off:off + len(old)])
        if cur != old:
            raise SystemExit(f"[{name}] @0x{off:05x} 原始位元組不符：預期 {old.hex(' ')}，實際 {cur.hex(' ')}。中止，未寫出檔案。")
        assert len(old) == len(new)
    for name, off, old, new in patches:
        data[off:off + len(new)] = new
        print(f"✓ {name} @0x{off:05x}: {old.hex(' ')} → {new.hex(' ')}")

    Path(a.dst).write_bytes(bytes(data))
    print(f"已寫出 {a.dst}（{len(data)} bytes，sha256 {hashlib.sha256(data).hexdigest()[:16]}…）")
    print("請用 fwinspect.py 檢查修補後的廣播結構。")


if __name__ == "__main__":
    main()
