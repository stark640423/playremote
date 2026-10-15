#!/usr/bin/env python3
"""PlayRemote 麥克風錄音工具 (Google ATVV 協定，1.0 模式 Hold-To-Talk)。

用法:
  audio.py [--outdir captures] [--name PlayRemote] [--legacy]

連線後會先送 ATVV 1.0 的 GET_CAPS，遙控器就切成「按住說話、放開即停、16 kHz」。
之後每按住一次麥克風鍵就存成一個 WAV (captures/voice-YYYYmmdd-HHMMSS.wav)。Ctrl+C 結束。
--legacy 不送 GET_CAPS，遙控器維持 0.4 模式 (按一下送 18 秒，8 kHz，有表頭幀)。

兩種幀格式 (實測，詳見 NOTES.md「ATVV 0.4 / 1.0 模式」):
  0.4: 每筆 134 bytes = seq(2,BE) id(1) pred(2,BE signed) step(1) + 128 bytes IMA ADPCM，8 kHz，每幀重設解碼器。
  1.0: 每筆 128 bytes 純 IMA ADPCM (無表頭)，8 或 16 kHz 依 AUDIO_START 的 codec，解碼器狀態跨幀延續，
       每 20 幀一筆 AUDIO_SYNC `0a codec seq(2) pred(2) step(1)` 可用來重設。
  兩種都是高 nibble 先。

放開的判斷：AUDIO_STOP 有時會延遲 15 秒才到，所以同時用「0.5 秒沒有新幀」當結束條件。

這個檔案的 AtvvMic 類別可以直接給之後的驅動程式用 (callback: 按下 / 放開 / 一段完整的 PCM)。
"""
import argparse
import asyncio
import struct
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from probe import open_client  # 重用連線邏輯 (含系統端裝置取用與重試)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ATVV_TX = "ab5e0002-5a21-4f05-bc7d-af01f617b664"   # 主機 → 遙控器 指令
ATVV_RX = "ab5e0003-5a21-4f05-bc7d-af01f617b664"   # 音訊資料
ATVV_CTL = "ab5e0004-5a21-4f05-bc7d-af01f617b664"  # 控制訊息
SAMPLE_RATE = 8000                                  # 0.4 模式的取樣率 (舊工具相容)
CODEC_RATE = {0x01: 8000, 0x02: 16000}
FRAME_GAP_END = 0.5                                 # 超過這麼久沒幀就當作放開

# ATVV 1.0 GET_CAPS: version 1.0, reserved, codecs 8k|16k, 互動模式 PTT|HTT
CMD_GET_CAPS_V10 = bytes([0x0A, 0x01, 0x00, 0x00, 0x03, 0x03])

CTL_NAMES = {0x00: "AUDIO_STOP", 0x04: "AUDIO_START", 0x07: "(廠商自訂 07)", 0x08: "START_SEARCH",
             0x0A: "AUDIO_SYNC", 0x0B: "CAPS_RESP", 0x0C: "MIC_OPEN_ERROR"}
STOP_REASON = {0x00: "MIC_CLOSE", 0x02: "HTT 放開", 0x04: "即將有新串流", 0x08: "傳輸逾時", 0x10: "通知被關閉"}

STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767,
]
INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


class ImaAdpcm:
    def __init__(self):
        self.pred = 0
        self.index = 0

    def reset(self, pred: int, index: int):
        self.pred = pred
        self.index = max(0, min(88, index))

    def decode_nibble(self, n: int) -> int:
        step = STEP_TABLE[self.index]
        diff = step >> 3
        if n & 4:
            diff += step
        if n & 2:
            diff += step >> 1
        if n & 1:
            diff += step >> 2
        if n & 8:
            self.pred -= diff
        else:
            self.pred += diff
        self.pred = max(-32768, min(32767, self.pred))
        self.index = max(0, min(88, self.index + INDEX_TABLE[n]))
        return self.pred

    def decode_frame(self, frame: bytes) -> bytes:
        """解一筆 0.4 格式 (有表頭) 的音訊通知，回傳 16-bit little-endian PCM。"""
        if len(frame) < 6:
            return b""
        _seq, _sid, pred, index = struct.unpack(">HBhB", frame[:6])
        self.reset(pred, index)
        return self.decode_raw(frame[6:])

    def decode_raw(self, data: bytes) -> bytes:
        """解純 ADPCM 位元組 (1.0 格式)，狀態延續不重設。"""
        out = []
        for b in data:
            out.append(self.decode_nibble(b >> 4))   # 高 nibble 先
            out.append(self.decode_nibble(b & 0x0F))
        return struct.pack("<%dh" % len(out), *out)


class StreamDecoder:
    """把 0.4 (有表頭) 和 1.0 (無表頭 + AUDIO_SYNC) 兩種幀格式都解成 PCM。"""

    def __init__(self):
        self.dec = ImaAdpcm()
        self.pcm = bytearray()
        self.rate = SAMPLE_RATE

    def on_start(self, codec: int):
        self.rate = CODEC_RATE.get(codec, SAMPLE_RATE)
        self.dec.reset(0, 0)

    def on_sync(self, msg: bytes):
        if len(msg) >= 7:
            pred = int.from_bytes(msg[4:6], "big", signed=True)
            self.dec.reset(pred, msg[6])

    def on_frame(self, frame: bytes) -> bytes:
        chunk = self.dec.decode_frame(frame) if len(frame) == 134 else self.dec.decode_raw(frame)
        self.pcm += chunk
        return chunk

    def take(self) -> bytes:
        pcm, self.pcm = bytes(self.pcm), bytearray()
        return pcm


class AtvvMic:
    """訂閱 ATVV，把語音鍵的按住/放開變成事件，並把每段錄音的 PCM 交給 callback。

    on_press()                  收到 AUDIO_START (語音鍵按下)
    on_release()                放開 (AUDIO_STOP，或 FRAME_GAP_END 秒沒幀)
    on_utterance(pcm, rate)     一段完整錄音 (16-bit mono little-endian)
    on_pcm(chunk, rate)         每收到一幀就給解好的 PCM (串流用，例如灌進虛擬麥克風)
    """

    def __init__(self, client, on_utterance: Callable[[bytes, int], None],
                 on_press: Optional[Callable[[], None]] = None,
                 on_release: Optional[Callable[[], None]] = None, log=print,
                 on_pcm: Optional[Callable[[bytes, int], None]] = None):
        self.client = client
        self.on_utterance = on_utterance
        self.on_press = on_press or (lambda: None)
        self.on_release = on_release or (lambda: None)
        self.on_pcm = on_pcm
        self.log = log
        self.dec = StreamDecoder()
        self.active = False
        self.frames = 0
        self.last_frame: Optional[float] = None
        self.caps: Optional[bytes] = None
        self.last_codec = 0x01
        self.negotiate_v10 = True
        self._caps_resent = 0

    async def start(self, negotiate_v10: bool = True):
        self.negotiate_v10 = negotiate_v10
        await self.client.start_notify(ATVV_CTL, self._on_ctl)
        await self.client.start_notify(ATVV_RX, self._on_audio)
        if negotiate_v10:
            await self.client.write_gatt_char(ATVV_TX, CMD_GET_CAPS_V10, response=False)
            self.log("已送 GET_CAPS 1.0 (遙控器會在 ~15 秒內回覆，之後語音鍵是按住說話)")

    async def tick(self):
        """主迴圈要定期呼叫 (例如每 0.1 秒)，用來偵測「沒幀了」。"""
        if self.active and self.last_frame is not None and time.monotonic() - self.last_frame > FRAME_GAP_END:
            self._finish("沒有新幀")

    def _on_ctl(self, _s, data: bytearray):
        data = bytes(data)
        if not data:
            return
        op = data[0]
        if op == 0x04 and len(data) >= 4:
            if self.active:
                self._finish("新的 AUDIO_START")
            self.active, self.frames, self.last_frame = True, 0, time.monotonic()
            self.last_codec = data[2]
            self.dec.on_start(data[2])
            self.log(f"按下 (codec {CODEC_RATE.get(data[2], '?')} Hz, stream {data[3]})")
            self.on_press()
        elif op == 0x00:
            reason = STOP_REASON.get(data[1], hex(data[1])) if len(data) > 1 else "0.4 格式"
            if self.active:
                self._finish(f"AUDIO_STOP ({reason})")
        elif op == 0x0A:
            self.dec.on_sync(data)
        elif op == 0x0B:
            self.caps = data
            if data[1:3] == b"\x01\x00":
                model = {0x00: "On-request", 0x01: "PTT", 0x03: "HTT"}.get(data[4], hex(data[4]))
                self.log(f"CAPS_RESP: 1.0 版, codecs={data[3]:#04x}, 互動模式={model}, 幀={int.from_bytes(data[5:7], 'big')}")
            else:
                self.log(f"CAPS_RESP: {data.hex(' ')} (0.4 版)")
        elif op == 0x0C:
            self.log(f"MIC_OPEN_ERROR {data[1:3].hex()}")
        else:
            self.log(f"CTL {data.hex(' ')} {CTL_NAMES.get(op, '?')}")

    def _on_audio(self, _s, data: bytearray):
        data = bytes(data)
        if not self.active:                      # AUDIO_START 晚到或 STOP 早到；先當作開始，取樣率沿用上次
            self.active, self.frames = True, 0
            self.dec.on_start(0x01 if len(data) == 134 else self.last_codec)
            self.on_press()
        self.frames += 1
        self.last_frame = time.monotonic()
        if len(data) == 134 and self.negotiate_v10 and self.frames == 1 and self._caps_resent < 3:
            # 遙控器重開機 (例如換電池) 後會回到 0.4 模式 (按一下送 18 秒)，之前送的 GET_CAPS 失效，再送一次
            self._caps_resent += 1
            self.log("收到 0.4 格式的幀，重送 GET_CAPS 1.0 (遙控器可能重開機過)")
            try:
                asyncio.get_running_loop().create_task(
                    self.client.write_gatt_char(ATVV_TX, CMD_GET_CAPS_V10, response=False))
            except RuntimeError:
                pass
        chunk = self.dec.on_frame(data)
        if self.on_pcm and chunk:
            self.on_pcm(chunk, self.dec.rate)

    def _finish(self, why: str):
        self.active = False
        pcm = self.dec.take()
        secs = len(pcm) / 2 / self.dec.rate
        self.log(f"放開 ({why})：{self.frames} 幀, {secs:.1f} 秒 @ {self.dec.rate} Hz")
        self.on_release()
        if pcm:
            self.on_utterance(pcm, self.dec.rate)


async def main(args):
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    def save(pcm: bytes, rate: int):
        out = outdir / f"voice-{datetime.now():%Y%m%d-%H%M%S}.wav"
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        print(f"  已存檔: {out}")

    async with open_client(args.name) as client:
        mic = AtvvMic(client, on_utterance=save)
        await mic.start(negotiate_v10=not args.legacy)
        print("已連線。按住麥克風鍵說話，放開就會存檔。Ctrl+C 結束。")
        try:
            while client.is_connected:
                await asyncio.sleep(0.1)
                await mic.tick()
        except asyncio.CancelledError:
            pass
        print("遙控器已斷線。" if not client.is_connected else "")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--outdir", default=str(Path(__file__).parent / "captures"))
    p.add_argument("--name", default="PlayRemote")
    p.add_argument("--legacy", action="store_true", help="不送 GET_CAPS，維持 0.4 模式")
    try:
        asyncio.run(main(p.parse_args()))
    except KeyboardInterrupt:
        print("\n結束。")
