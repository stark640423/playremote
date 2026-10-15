"""Telink OTA 探測（實驗工具）：只送 OTA start 與前幾個資料封包，不送 end。

用途：搭配燒錄器讀出整片前後比對，找出這支遙控器的 OTA 實際寫入位址
（ota_program_offset），判斷標準 OTA 會不會蓋到 BLE 韌體尾端或 0x40000 的 2.4G 映像。

    python ota_probe.py captures/playremote_after_ccc.bin --packets 16

映像來源是整片 dump：前 4K 取 0x31C00 的副本（0x0 平常是空的），其後取 dump 本身。
封包格式（Telink legacy OTA）：index(2, LE) + 16 bytes 資料 + CRC16/MODBUS(前 18 bytes, LE)。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from bleak import BleakClient, BleakScanner

OTA_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b12"
CMD_OTA_START = b"\x01\xff"


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def ota_packet(index: int, chunk: bytes) -> bytes:
    body = index.to_bytes(2, "little") + chunk.ljust(16, b"\xff")
    return body + crc16(body).to_bytes(2, "little")


def bank0_image(dump: bytes) -> bytes:
    return dump[0x31C00:0x32C00] + dump[0x1000:]


async def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("dump", help="整片 dump（512 KB）")
    ap.add_argument("--packets", type=int, default=16, help="送幾個資料封包（每個 16 bytes）")
    ap.add_argument("--name", default="PlayRemote")
    ap.add_argument("--address", help="直接指定藍牙位址（裝置已與本機連線、沒有在廣播時用）")
    ap.add_argument("--interval", type=float, default=0.03, help="封包間隔秒數")
    args = ap.parse_args()

    image = bank0_image(Path(args.dump).read_bytes())
    if image[8:12] != b"KNLT":
        sys.exit(f"映像 0x8 不是 KNLT：{image[8:12].hex(' ')}")

    if args.address:
        dev = args.address
    else:
        print(f"掃描 {args.name}（請讓遙控器在配對模式）…")
        dev = await BleakScanner.find_device_by_name(args.name, timeout=20)
        if not dev:
            sys.exit("找不到裝置")
    print("找到：", dev)
    client = BleakClient(dev)
    if args.address and sys.platform == "win32":
        # 已與 Windows 連線的裝置不廣播，bleak 的掃描找不到；直接給 WinRT 位址跳過掃描
        client._backend._device_info = int(args.address.replace(":", ""), 16)
    async with client as c:
        print("已連線")
        await c.write_gatt_char(OTA_CHAR, CMD_OTA_START, response=False)
        print("已送 OTA start (0xFF01)")
        await asyncio.sleep(0.5)
        for i in range(args.packets):
            pkt = ota_packet(i, image[i * 16:(i + 1) * 16])
            await c.write_gatt_char(OTA_CHAR, pkt, response=False)
            await asyncio.sleep(args.interval)
        print(f"已送 {args.packets} 個資料封包（{args.packets * 16} bytes），不送 OTA end")
        await asyncio.sleep(2)
    print("已斷線。接回燒錄器讀出整片，與 dump 比對。")


if __name__ == "__main__":
    asyncio.run(main())
