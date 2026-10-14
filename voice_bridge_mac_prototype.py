#!/usr/bin/env python3
"""PlayRemote 語音橋接（模式二）：遙控器麥克風 → 虛擬麥克風 + 觸發鍵。

流程:
  遙控器 AUDIO_START → 模擬按下觸發鍵 (預設 Fn)
  音訊幀 → IMA ADPCM 解碼 → 8 kHz 升頻到裝置取樣率 → 即時送進虛擬麥克風 (預設 BlackHole 2ch)
  遙控器 AUDIO_STOP → 釋放觸發鍵

用法:
  voice_bridge.py [--device "BlackHole 2ch"] [--key fn|none] [--list-devices] [--no-key]

需要:
  - 已安裝 BlackHole 2ch (或任何回環音訊裝置)，語音輸入法把麥克風選成同一個裝置。
  - 模擬按鍵需要「輔助使用」權限 (系統設定 → 隱私權與安全性 → 輔助使用 → 允許終端機)。
"""
import argparse
import asyncio
import queue
import sys
import time

import numpy as np
import sounddevice as sd

from audio import ATVV_CTL, ATVV_RX, SAMPLE_RATE, ImaAdpcm
from probe import open_client

DEFAULT_DEVICE = "BlackHole 2ch"

# macOS 虛擬鍵碼
KEYCODES = {"fn": 63, "rcmd": 54, "lcmd": 55, "ropt": 61, "lopt": 58, "f18": 79, "f19": 80, "f20": 90}


class KeyPresser:
    """用 Quartz CGEvent 模擬按住/放開一個鍵。macOS 專用；其他平台為空操作。"""

    def __init__(self, key: str):
        self.key = key
        self.enabled = key != "none" and sys.platform == "darwin"
        if self.enabled:
            import Quartz  # noqa: WPS433
            self.Q = Quartz
            if key not in KEYCODES:
                raise SystemExit(f"不支援的鍵: {key}，可用: {', '.join(KEYCODES)}")
            self.code = KEYCODES[key]

    def _post(self, down: bool):
        if not self.enabled:
            return
        ev = self.Q.CGEventCreateKeyboardEvent(None, self.code, down)
        if self.key == "fn":
            # Fn 是修飾鍵，要同時設定 flag 才會被當成 Fn 按住
            self.Q.CGEventSetFlags(ev, self.Q.kCGEventFlagMaskSecondaryFn if down else 0)
        self.Q.CGEventPost(self.Q.kCGHIDEventTap, ev)

    def press(self):
        self._post(True)

    def release(self):
        self._post(False)


def find_output_device(name: str) -> tuple[int, int]:
    """回傳 (device index, samplerate)。找不到就列出可用裝置並結束。"""
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d["name"].lower() and d["max_output_channels"] > 0:
            return i, int(d["default_samplerate"])
    print(f"找不到輸出裝置「{name}」。可用的輸出裝置：")
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            print(f"  [{i}] {d['name']}")
    print("\n請安裝 BlackHole 2ch (https://existential.audio/blackhole/) 或用 --device 指定。")
    raise SystemExit(1)


async def main(args):
    if args.list_devices:
        print(sd.query_devices())
        return

    dev_index, dev_rate = find_output_device(args.device)
    ratio = dev_rate // SAMPLE_RATE  # 48000 / 8000 = 6
    if dev_rate % SAMPLE_RATE:
        raise SystemExit(f"裝置取樣率 {dev_rate} 不是 {SAMPLE_RATE} 的整數倍，暫不支援。")

    key = KeyPresser("none" if args.no_key else args.key)
    dec = ImaAdpcm()
    pcm_q: "queue.Queue[np.ndarray]" = queue.Queue()
    leftover = np.zeros(0, dtype=np.float32)
    stats = {"frames": 0, "active": False, "t0": 0.0}

    def upsample(x: np.ndarray) -> np.ndarray:
        # 線性內插升頻，夠用；語音輸入法會自己重採樣
        n = len(x)
        src = np.arange(n)
        dst = np.arange(n * ratio) / ratio
        return np.interp(dst, src, x).astype(np.float32)

    def audio_cb(outdata, frames, _time, status):
        nonlocal leftover
        if status:
            print("audio:", status, file=sys.stderr)
        buf = leftover
        while len(buf) < frames:
            try:
                buf = np.concatenate([buf, pcm_q.get_nowait()])
            except queue.Empty:
                break
        if len(buf) >= frames:
            out, leftover = buf[:frames], buf[frames:]
        else:
            out = np.concatenate([buf, np.zeros(frames - len(buf), dtype=np.float32)])
            leftover = np.zeros(0, dtype=np.float32)
        outdata[:] = np.repeat(out[:, None], outdata.shape[1], axis=1)

    def on_ctl(_s, data: bytearray):
        code = data[0] if data else None
        if code == 0x04:
            stats["active"] = True
            stats["t0"] = time.monotonic()
            stats["frames"] = 0
            key.press()
            print("● 開始說話（已按下觸發鍵）")
        elif code == 0x00:
            stats["active"] = False
            # 讓佇列裡的聲音播完再放開，否則輸入法會少掉最後半秒
            delay = max(0.0, pcm_q.qsize() * 0.03) + 0.25
            asyncio.get_event_loop().call_later(delay, key.release)
            dur = time.monotonic() - stats["t0"]
            print(f"○ 結束（{dur:.1f} 秒, {stats['frames']} 幀），{delay:.2f} 秒後釋放觸發鍵")

    def on_audio(_s, data: bytearray):
        stats["frames"] += 1
        pcm = np.frombuffer(dec.decode_frame(bytes(data)), dtype=np.int16).astype(np.float32) / 32768.0
        pcm_q.put(upsample(pcm))

    stream = sd.OutputStream(device=dev_index, samplerate=dev_rate, channels=2, dtype="float32",
                             blocksize=256, callback=audio_cb)
    with stream:
        async with open_client(args.name) as client:
            await client.start_notify(ATVV_CTL, on_ctl)
            await client.start_notify(ATVV_RX, on_audio)
            print(f"已連線。聲音 → 「{sd.query_devices(dev_index)['name']}」({dev_rate} Hz)，"
                  f"觸發鍵: {'無' if not key.enabled else key.key}。按住遙控器語音鍵說話，Ctrl+C 結束。")
            try:
                while client.is_connected:
                    await asyncio.sleep(0.5)
                print("遙控器已斷線。")
            except asyncio.CancelledError:
                pass
            finally:
                key.release()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--device", default=DEFAULT_DEVICE, help="輸出到哪個 (虛擬) 音訊裝置")
    p.add_argument("--key", default="fn", help="觸發鍵: " + ", ".join(KEYCODES) + ", none")
    p.add_argument("--no-key", action="store_true", help="不模擬按鍵，只送聲音")
    p.add_argument("--name", default="PlayRemote")
    p.add_argument("--list-devices", action="store_true")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        print("\n結束。")
