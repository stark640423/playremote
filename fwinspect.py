#!/usr/bin/env python3
"""PlayRemote（Telink TLSR8251）韌體檔分析工具。只讀，不會修改任何檔案。

用法:
  fwinspect.py 韌體.bin [--compare 另一個.bin]

會找:
- Telink 檔頭：0x08 的 "KNLT" 標記、0x18 的映像長度（多個 bank 時每個都列）
- 廣播資料候選：裝置名稱 "PlayRemote"（含 AD 長度前綴）、"KMSWand"、
  Appearance (AD 0x19)、16-bit UUID 清單含 0x1812 (AD 0x02/0x03)、Flags (AD 0x01)、
  Manufacturer data 0x005D (AD 0xFF)
- 每個候選位置前後各 48 bytes 的 hex，並嘗試把該處解析成 AD 結構
- --compare：兩次讀出的韌體是否完全相同（確認讀取穩定）
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import zlib
from pathlib import Path

AD_NAMES = {0x01: "Flags", 0x02: "UUID16(部分)", 0x03: "UUID16(完整)", 0x06: "UUID128(部分)", 0x07: "UUID128(完整)",
            0x08: "名稱(縮寫)", 0x09: "名稱(完整)", 0x0A: "TxPower", 0x12: "連線間隔", 0x16: "ServiceData16",
            0x19: "Appearance", 0xFF: "廠商資料"}


def hexdump(data: bytes, base: int, start: int, end: int, mark: tuple[int, int] | None = None) -> str:
    out = []
    start = max(0, start & ~0xF)
    end = min(len(data), end)
    for off in range(start, end, 16):
        chunk = data[off:off + 16]
        hx = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        flag = " <" if mark and off <= mark[0] < off + 16 else ""
        out.append(f"  {base + off:08x}  {hx:<47}  {asc}{flag}")
    return "\n".join(out)


def parse_ad(data: bytes, off: int, limit: int = 31) -> list[str]:
    """從 off 開始嘗試解析成一串 AD 結構，最多 limit bytes；不合理就停。"""
    items, i = [], off
    while i < len(data) and i - off < limit:
        ln = data[i]
        if ln == 0 or ln > 30 or i + 1 + ln > len(data):
            break
        typ = data[i + 1]
        val = data[i + 2:i + 1 + ln]
        name = AD_NAMES.get(typ)
        if name is None:
            break
        if typ in (0x08, 0x09):
            v = val.decode("ascii", "replace")
        elif typ == 0x19 and len(val) == 2:
            v = f"0x{struct.unpack('<H', val)[0]:04x}"
        elif typ in (0x02, 0x03):
            v = " ".join(f"0x{struct.unpack('<H', val[k:k+2])[0]:04x}" for k in range(0, len(val) - 1, 2))
        else:
            v = val.hex(" ")
        items.append(f"[{i - off:2d}] len={ln:2d} {name}: {v}")
        i += 1 + ln
    if items:
        items.append(f"共 {i - off} bytes（廣播主封包上限 31）")
    return items


def find_all(data: bytes, needle: bytes) -> list[int]:
    res, i = [], data.find(needle)
    while i != -1:
        res.append(i)
        i = data.find(needle, i + 1)
    return res


def main():
    # Windows 中文主控台預設 cp950，印不出 ✓ 等字元
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("bin")
    ap.add_argument("--compare")
    a = ap.parse_args()
    data = Path(a.bin).read_bytes()
    print(f"檔案: {a.bin}  {len(data)} bytes (0x{len(data):x})  sha256={hashlib.sha256(data).hexdigest()[:16]}…")
    if a.compare:
        other = Path(a.compare).read_bytes()
        same = other == data
        print(f"與 {a.compare} 比較: {'完全相同' if same else '不同！'}")
        if not same:
            diffs = [i for i in range(min(len(data), len(other))) if data[i] != other[i]]
            print(f"  長度 {len(data)} vs {len(other)}，相異 bytes {len(diffs)} 個，前幾個位置: {[hex(d) for d in diffs[:10]]}")

    blank = data.count(0xFF) / max(1, len(data))
    print(f"0xFF 比例: {blank:.0%}（整片讀出時，未使用區域應為 0xFF）")

    print("\n== Telink 檔頭 (KNLT) ==")
    for off in find_all(data, b"KNLT"):
        base = off - 8
        if base < 0 or base % 0x1000:
            continue
        size = struct.unpack_from("<I", data, base + 0x18)[0]
        info = f"  bank @0x{base:05x}: 映像長度 0x{size:x} ({size} bytes)"
        if 0 < size <= len(data) - base:
            img = data[base:base + size]
            info += f"，CRC32(全映像)=0x{zlib.crc32(img) & 0xffffffff:08x}"
            info += f"，末 4 bytes=0x{struct.unpack_from('<I', data, base + size - 4)[0]:08x}"
        print(info)

    probes = [
        ("裝置名稱 PlayRemote（AD 完整名稱 0b 09）", b"\x0b\x09PlayRemote"),
        ("字串 PlayRemote", b"PlayRemote"),
        ("字串 KMSWand", b"KMSWand"),
        ("Appearance 0x0180（AD 03 19 80 01）", b"\x03\x19\x80\x01"),
        ("Appearance AD 型別 (03 19 ..)", None),
        ("UUID16 清單含 1812（03 03 12 18 / 03 02 12 18）", None),
        ("廠商資料 0x005D（.. ff 5d 00）", None),
        ("Flags（02 01 05 / 02 01 06）", None),
    ]
    print("\n== 廣播資料候選 ==")
    hits: dict[int, str] = {}
    for label, needle in probes:
        if needle is not None:
            locs = find_all(data, needle)
        elif "Appearance AD" in label:
            locs = [i for i in find_all(data, b"\x03\x19") if i + 3 < len(data)]
        elif "1812" in label:
            locs = find_all(data, b"\x03\x03\x12\x18") + find_all(data, b"\x03\x02\x12\x18") + find_all(data, b"\x05\x02\x12\x18") + find_all(data, b"\x05\x03\x12\x18")
        elif "005D" in label:
            locs = [i - 1 for i in find_all(data, b"\xff\x5d\x00") if i > 0]
        else:
            locs = find_all(data, b"\x02\x01\x05") + find_all(data, b"\x02\x01\x06")
        locs = sorted(set(locs))
        print(f"- {label}: {len(locs)} 處 {[hex(x) for x in locs[:12]]}{' …' if len(locs) > 12 else ''}")
        for x in locs[:12]:
            hits.setdefault(x, label)

    # 把相近的命中合併成區塊顯示
    generic = ("Appearance AD 型別", "Flags", "UUID16", "廠商資料")
    shown_until = -1
    for off in sorted(hits):
        if off <= shown_until:
            continue
        ad, ad_start = [], None
        for start in range(max(0, off - 24), off + 1):
            cand = parse_ad(data, start)
            if len(cand) >= 3:
                ad, ad_start = cand, start
                break
        if hits[off].startswith(generic) and not ad:
            continue                                  # 短樣式在程式碼區常誤中，解析不出廣播結構就略過
        print(f"\n--- 0x{off:05x} 附近（{hits[off]}）")
        print(hexdump(data, 0, off - 48, off + 64, (off, off)))
        if ad:
            print(f"  可解析為 AD 結構（從 0x{ad_start:05x} 起）:")
            for line in ad:
                print("    " + line)
        shown_until = off + 64


if __name__ == "__main__":
    main()
