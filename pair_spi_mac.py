#!/usr/bin/env python3
"""macOS 實驗：用 CoreBluetooth 私有的 PairingAgent SPI 直接對 PlayRemote 發起 LE 配對。

背景：系統設定對 PlayRemote 不提供「連接」，因為它只用 ADV 主封包的 Appearance 分類裝置。
但設定頁實際配對用的是 CBCentralManager.sharedPairingAgent → CBPairingAgent.pairPeer:options:。
這支程式連上遙控器後直接呼叫同一個 SPI，看一般 App 能不能成功。

用法:
  pair_spi_mac.py [--name PlayRemote] [--mitm]

成功的判準：系統設定 → 藍牙 的「我的裝置」出現 PlayRemote；`hidutil list` 看得到 VID 0x2D01。
"""
from __future__ import annotations

import argparse
import asyncio
import time

import objc
from Foundation import NSObject

from probe import open_client


class AgentDelegate(NSObject):
    """盡量把 PairingAgent 丟回來的任何訊息印出來，並自動接受配對請求。"""

    def init(self):
        self = objc.super(AgentDelegate, self).init()
        self.events = []
        return self

    def _log(self, *a):
        msg = " ".join(str(x) for x in a)
        self.events.append(msg)
        print("  [agent]", msg)

    # 可能的 delegate 回呼名稱（私有 API 不公開，盡量多接幾個）
    def pairingAgent_peerDidUpdatePairingStatus_(self, agent, peer):
        self._log("peerDidUpdatePairingStatus", peer)

    def pairingAgent_peerDidCompletePairing_(self, agent, peer):
        self._log("peerDidCompletePairing", peer)

    def pairingAgent_peerDidFailToCompletePairing_error_(self, agent, peer, error):
        self._log("peerDidFailToCompletePairing", peer, error)

    def pairingAgent_peerDidUnpair_(self, agent, peer):
        self._log("peerDidUnpair", peer)

    def pairingAgent_peerDidRequestPairing_type_passkey_(self, agent, peer, ptype, passkey):
        self._log("peerDidRequestPairing type", ptype, "passkey", passkey, "→ 自動接受")
        try:
            agent.respondToPairingRequest_type_accept_data_(peer, ptype, True, None)
        except Exception as e:  # noqa: BLE001
            self._log("respond failed:", e)

    def pairingAgent_peerDidRequestPairing_type_(self, agent, peer, ptype):
        self._log("peerDidRequestPairing type", ptype, "→ 自動接受")
        try:
            agent.respondToPairingRequest_type_accept_data_(peer, ptype, True, None)
        except Exception as e:  # noqa: BLE001
            self._log("respond failed:", e)


def list_selectors(obj, keyword=""):
    try:
        return sorted({m.selector.decode() for m in type(obj).pyobjc_instanceMethods.__dict__.values()
                       if hasattr(m, "selector") and keyword.lower() in m.selector.decode().lower()})
    except Exception:  # noqa: BLE001
        return []


async def main(args):
    async with open_client(args.name) as client:
        backend = client._backend
        peripheral = backend._peripheral
        cm = backend._central_manager_delegate.central_manager
        print(f"已連線: {client.address}")
        print("CBCentralManager:", cm)

        agent = None
        try:
            agent = cm.sharedPairingAgent()
        except Exception as e:  # noqa: BLE001
            print("sharedPairingAgent 呼叫失敗:", e)
        if agent is None:
            print("拿不到 PairingAgent（回傳 nil）。這表示此 SPI 需要系統 entitlement，一般 App 無法使用。")
            return
        print("PairingAgent:", agent)
        print("agent selectors:", list_selectors(agent))

        delegate = AgentDelegate.alloc().init()
        for sel in ("setDelegate_",):
            if hasattr(agent, sel):
                try:
                    getattr(agent, sel)(delegate)
                    print("已設定 delegate")
                except Exception as e:  # noqa: BLE001
                    print("setDelegate 失敗:", e)

        try:
            paired = agent.isPeerPaired_(peripheral)
            print("isPeerPaired (配對前):", paired)
        except Exception as e:  # noqa: BLE001
            print("isPeerPaired 失敗:", e)

        print("\n呼叫 pairPeer ... 若跳出系統配對視窗請按「連線」。")
        try:
            if args.mitm:
                r = agent.pairPeer_useMITM_(peripheral, True)
            else:
                r = agent.pairPeer_options_(peripheral, None)
            print("pairPeer 回傳:", r)
        except Exception as e:  # noqa: BLE001
            print("pairPeer 呼叫失敗:", e)
            return

        t0 = time.monotonic()
        while time.monotonic() - t0 < 40 and client.is_connected:
            await asyncio.sleep(1)
            try:
                p = agent.isPeerPaired_(peripheral)
            except Exception:  # noqa: BLE001
                p = "?"
            if int(time.monotonic() - t0) % 5 == 0:
                print(f"  {time.monotonic()-t0:4.0f}s  isPeerPaired={p}  connected={client.is_connected}")
            if p is True:
                print("\n配對成功！請看系統設定 → 藍牙 的「我的裝置」，並用 hidutil list 確認。")
                break
        else:
            print("\n40 秒內沒有完成配對。請把上面的輸出整段貼回，並說明有沒有跳出任何視窗。")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--name", default="PlayRemote")
    p.add_argument("--mitm", action="store_true", help="改用 pairPeer:useMITM:")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        print("\n結束。")
