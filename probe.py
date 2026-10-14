#!/usr/bin/env python3
"""PlayRemote BLE 探測工具。

用法:
  probe.py scan                      掃描附近的 BLE 裝置
  probe.py dump  [--name PlayRemote]  連線並列出所有 Service / Characteristic
  probe.py watch [--name PlayRemote]  連線、訂閱所有 notify，印出原始位元組
                                      (同時寫入 captures/*.log)

macOS 上 bleak 看到的是 CoreBluetooth UUID，不是 MAC 位址，所以用名稱比對。
Windows 上遙控器配對後會被系統接住 (HID)，不再廣播，所以直接用 MAC 位址連線。
"""
import argparse
import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 主控台預設 cp950

DEFAULT_NAME = "PlayRemote"
# 可選：固定用某個裝置 (環境變數 PLAYREMOTE_ADDRESS = MAC 位址 / macOS 的 CoreBluetooth UUID)。
# 沒設就用名稱自動找已配對 / 已連線的裝置。
KNOWN_ADDRESS = os.environ.get("PLAYREMOTE_ADDRESS", "")


async def find_paired_address_windows(name: str) -> str | None:
    """Windows: 用 WinRT 列出已配對的 BLE 裝置，回傳名稱相符者的 MAC (AA:BB:CC:DD:EE:FF)。"""
    from winrt.windows.devices.bluetooth import BluetoothLEDevice
    from winrt.windows.devices.enumeration import DeviceInformation

    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    infos = await DeviceInformation.find_all_async_aqs_filter(selector)
    for info in infos:
        if name.lower() not in (info.name or "").lower():
            continue
        dev = await BluetoothLEDevice.from_id_async(info.id)
        if dev is None:
            continue
        a = dev.bluetooth_address
        return ":".join(f"{(a >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))
    return None


async def find_system_connected(name: str):
    """找出已經被 macOS 連上 (在系統藍牙設定裡顯示「已連線」) 的裝置。

    已連線的 BLE 裝置不會再廣播，一般掃描看不到，要用 retrieveConnectedPeripherals。

    Windows: 配對過的裝置可以直接用位址連線 (WinRT 的 from_bluetooth_address_async)，
    不必掃描；bleak 只有拿到 BLEDevice 物件時才走這條路，所以自己包一個。
    """
    if sys.platform == "win32":
        addr = KNOWN_ADDRESS or await find_paired_address_windows(name)
        if not addr:
            print(f"Windows 的已配對裝置裡找不到 '{name}'，請先在「設定 → 藍牙」配對。")
            return None
        return BLEDevice(addr, name, None)
    if sys.platform != "darwin":
        return None
    from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate
    from CoreBluetooth import CBUUID

    from Foundation import NSUUID

    mgr = CentralManagerDelegate.alloc().init()
    # 1) 用指定的裝置 ID 直接取用 (CoreBluetooth identifier)
    if KNOWN_ADDRESS:
        ids = [NSUUID.alloc().initWithUUIDString_(KNOWN_ADDRESS)]
        for p in mgr.central_manager.retrievePeripheralsWithIdentifiers_(ids) or []:
            return BLEDevice(str(p.identifier().UUIDString()), p.name() or name, (p, mgr))
    # 2) 查 macOS 目前已連線、且有常見服務的裝置
    uuids = [CBUUID.UUIDWithString_(u) for u in
             ("1800", "1801", "180A", "180F", "1812", "1813")]
    for p in mgr.central_manager.retrieveConnectedPeripheralsWithServices_(uuids) or []:
        pname = p.name() or ""
        if name.lower() in pname.lower():
            return BLEDevice(str(p.identifier().UUIDString()), pname, (p, mgr))
    return None


async def find_device(name: str, timeout: float = 15.0):
    print(f"掃描 '{name}' (最多 {timeout:.0f} 秒)...")
    dev = None
    for attempt in range(1, 6):
        try:
            dev = await BleakScanner.find_device_by_filter(
                lambda d, adv: name.lower() in ((d.name or adv.local_name or "").lower()),
                timeout=timeout,
            )
            break
        except BleakError as e:
            # 首次執行時，系統授權對話框會讓 CoreBluetooth 狀態來不及回報，
            # bleak 只等 1 秒就誤報「Bluetooth device is turned off」，所以重試。
            if "not authorized" in str(e):
                print(f"{e}\n請到「系統設定 → 隱私權與安全性 → 藍牙」允許「終端機」。")
                return None
            print(f"  藍牙尚未就緒 ({e})，重試 {attempt}/5... "
                  "若出現授權對話框請按允許。")
            await asyncio.sleep(2)
    else:
        print("藍牙一直無法就緒。請確認「系統設定 → 隱私權與安全性 → 藍牙」"
              "裡「終端機」已開啟，必要時關掉再開，並重開終端機。")
        return None
    if dev is None:
        print(
            "找不到裝置。常見原因:\n"
            "  1. 已被系統或其他裝置 (iPhone/iPad) 連線，因此不再廣播。\n"
            "     請到「系統設定 → 藍牙」對 PlayRemote 按「忘記此裝置」，\n"
            "     並關閉其他裝置的藍牙後重試。\n"
            "  2. 遙控器休眠，按任意鍵喚醒或重新進入配對模式。\n"
            "  3. 終端機沒有藍牙權限 (系統設定 → 隱私權與安全性 → 藍牙)。"
        )
    return dev


@asynccontextmanager
async def open_client(name: str, tries: int = 6):
    """找到裝置後連線；連線逾時就重新掃描再試 (遙控器常在廣播後很快睡著)。"""
    for attempt in range(1, tries + 1):
        try:
            dev = await find_system_connected(name)
        except Exception as e:  # noqa: BLE001
            print(f"  (查詢系統已連線裝置失敗: {e})")
            dev = None
        if dev is not None:
            print(f"直接取用系統端的裝置物件: {dev.name} ({dev.address})")
        else:
            dev = await find_device(name, timeout=10.0)
        if dev is None:
            print(f"  (第 {attempt}/{tries} 次) 沒掃到，請按遙控器任意鍵...")
            continue
        client = BleakClient(dev, timeout=20.0, winrt={"use_cached_services": False})
        try:
            print(f"找到 {dev.name} ({dev.address})，連線中 (第 {attempt}/{tries} 次)，"
                  "請持續按遙控器按鍵保持喚醒...")
            await client.connect()
        except (asyncio.TimeoutError, BleakError, OSError) as e:
            print(f"  連線失敗: {type(e).__name__} {e}")
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1)
            continue
        try:
            yield client
        finally:
            if client.is_connected:
                await client.disconnect()
        return
    raise SystemExit(
        "多次嘗試仍無法連線。可能原因: 遙控器需要先在系統「配對」才允許連線、"
        "電量不足，或仍被其他裝置佔用。"
    )


async def cmd_scan(_args):
    print("掃描 10 秒...")
    found = await BleakScanner.discover(timeout=10.0, return_adv=True)
    rows = sorted(found.values(), key=lambda x: x[1].rssi, reverse=True)
    for dev, adv in rows:
        name = dev.name or adv.local_name or "(無名稱)"
        print(f"{adv.rssi:>4} dBm  {name:<28} {dev.address}")
        if adv.service_uuids:
            print(f"          services: {', '.join(adv.service_uuids)}")
        for cid, data in adv.manufacturer_data.items():
            print(f"          mfg 0x{cid:04X}: {data.hex(' ')}")


async def cmd_dump(args):
    async with open_client(args.name) as client:
        print(f"已連線: {client.address}  MTU={client.mtu_size}\n")
        for svc in client.services:
            print(f"[Service] {svc.uuid}  {svc.description}")
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                line = f"  [Char] {ch.uuid}  handle={ch.handle}  ({props})"
                if "read" in ch.properties:
                    try:
                        val = await client.read_gatt_char(ch)
                        text = bytes(val).decode("utf-8", "replace")
                        line += f"\n         值: {bytes(val).hex(' ')}"
                        if text.isprintable() and text:
                            line += f"  \"{text}\""
                    except Exception as e:  # noqa: BLE001
                        line += f"\n         讀取失敗: {e}"
                print(line)
                for d in ch.descriptors:
                    print(f"      [Desc] {d.uuid}  handle={d.handle}")
            print()


async def cmd_watch(args):
    outdir = Path(__file__).parent / "captures"
    outdir.mkdir(exist_ok=True)
    logfile = outdir / f"{datetime.now():%Y%m%d-%H%M%S}.log"
    start = time.monotonic()
    last: dict[str, bytes] = {}

    with logfile.open("w") as log:

        def make_handler(ch):
            label = f"{ch.uuid[:8]}(h{ch.handle})"

            def handler(_sender, data: bytearray):
                data = bytes(data)
                t = time.monotonic() - start
                prev = last.get(label)
                last[label] = data
                mark = " " if prev is None or prev != data else "="
                line = f"{t:9.3f}  {label:<18} {mark} len={len(data):<3} {data.hex(' ')}"
                print(line)
                log.write(line + "\n")
                log.flush()

            return handler

        async with open_client(args.name) as client:
            print(f"已連線: {client.address}，紀錄檔: {logfile}")
            subscribed = 0
            for svc in client.services:
                for ch in svc.characteristics:
                    if "notify" in ch.properties or "indicate" in ch.properties:
                        try:
                            await client.start_notify(ch, make_handler(ch))
                            subscribed += 1
                            print(f"  已訂閱 {ch.uuid} (h{ch.handle})")
                        except Exception as e:  # noqa: BLE001
                            print(f"  訂閱失敗 {ch.uuid}: {e}")
            if not subscribed:
                print("沒有任何可訂閱的特徵值。")
                return
            print(
                "\n開始記錄。請依序操作遙控器: 單獨按各個按鍵 → 轉動/搖晃 → "
                "滾輪 → 按住麥克風說話。\n"
                "每次只做一種動作並稍停，之後比較好對照。Ctrl+C 結束。\n"
                "欄位標記: ' ' = 資料有變化(或第一筆)，'=' = 與上一筆相同\n"
            )
            try:
                while client.is_connected:
                    await asyncio.sleep(0.5)
                print("遙控器已斷線。")
            except asyncio.CancelledError:
                pass


async def cmd_poke(args):
    """對自訂服務的可寫特徵值寫入候選啟用值，觀察是否開始有按鍵/感測資料。"""
    W_1920 = "00002b01-0000-1000-8000-00805f9b34fb"
    W_0AA0 = "0000b002-0000-1000-8000-00805f9b34fb"
    candidates = [
        (W_1920, bytes([0x01])), (W_1920, bytes([0x01, 0x00])), (W_1920, bytes([0xA1, 0x01])),
        (W_0AA0, bytes([0x01])), (W_0AA0, bytes([0x01, 0x00])), (W_0AA0, bytes([0xA1, 0x01])),
        (W_1920, bytes([0x02])), (W_0AA0, bytes([0x02])), (W_1920, bytes([0xFF])), (W_0AA0, bytes([0xFF])),
    ]
    start = time.monotonic()
    seen = {}

    def make_handler(ch):
        label = f"{ch.uuid[:8]}(h{ch.handle})"
        def handler(_s, data):
            seen[label] = seen.get(label, 0) + 1
            print(f"{time.monotonic()-start:9.3f}  {label:<18} len={len(data):<3} {bytes(data).hex(' ')}")
        return handler

    async with open_client(args.name) as client:
        for svc in client.services:
            for ch in svc.characteristics:
                if "notify" in ch.properties or "indicate" in ch.properties:
                    try:
                        await client.start_notify(ch, make_handler(ch))
                    except Exception as e:  # noqa: BLE001
                        print(f"  訂閱失敗 {ch.uuid}: {e}")
        print("已訂閱。每次寫入後有 8 秒，請連按 OK 鍵並轉動遙控器。\n")
        for uuid, val in candidates:
            try:
                await client.write_gatt_char(uuid, val, response=False)
                print(f"--- 已寫入 {uuid[:8]} <- {val.hex(' ')}  (請操作遙控器) ---")
            except Exception as e:  # noqa: BLE001
                print(f"--- 寫入 {uuid[:8]} <- {val.hex(' ')} 失敗: {e} ---")
                continue
            before = dict(seen)
            await asyncio.sleep(8)
            new = {k: v - before.get(k, 0) for k, v in seen.items() if v != before.get(k, 0)}
            print(f"    這段期間的新通知: {new or '無'}\n")
        print("實驗結束。")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    for n in ("dump", "watch", "poke"):
        sp = sub.add_parser(n)
        sp.add_argument("--name", default=DEFAULT_NAME)
    args = p.parse_args()
    fn = {"scan": cmd_scan, "dump": cmd_dump, "watch": cmd_watch, "poke": cmd_poke}[args.cmd]
    try:
        asyncio.run(fn(args))
    except KeyboardInterrupt:
        print("\n結束。")


if __name__ == "__main__":
    main()
