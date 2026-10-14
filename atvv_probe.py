#!/usr/bin/env python3
"""ATVV 麥克風行為實驗工具。

目的：弄清楚 PlayRemote 的語音鍵到底是「按一下送固定秒數」還是「按住說話」，
以及主機端的指令 (GET_CAPS / MIC_OPEN / MIC_CLOSE / MIC_EXTEND) 對它有沒有作用。

用法:
  atvv_probe.py                       只觀察 (遙控器維持 0.4 模式: 按一下送 18 秒)
  atvv_probe.py --caps                連線後先送 GET_CAPS (v0.4 格式)，看 CAPS_RESP
  atvv_probe.py --caps-v1             連線後先送 GET_CAPS (v1.0 格式) → 遙控器切成 Hold-To-Talk、16 kHz
  atvv_probe.py --close-after 3       收到 AUDIO_START 後 3 秒送 MIC_CLOSE，看串流會不會提早停
  atvv_probe.py --extend 5            串流中每 5 秒送 MIC_EXTEND
  atvv_probe.py --mic-open            連線 1 秒後主機主動送 MIC_OPEN (不按鍵能不能開麥)
  atvv_probe.py --send 0a0004000      連線後先寫入任意 hex 到 TX
  atvv_probe.py --wav                 另外把所有音訊存成 captures/atvv-*.wav (自動分辨 0.4 / 1.0 幀格式)

Ctrl+C 結束。所有控制訊息與原始音訊幀 (hex) 都寫進 captures/atvv-*.log，之後可離線重解。

已知行為 (2026-10-14 實測):
  0.4 模式 (預設): 按鍵 → AUDIO_START `04 03 01 id` → 134-byte 幀 (6-byte 表頭 + 128 ADPCM, 8 kHz)
                   固定送 18 秒，放開不會停，之後才送 AUDIO_STOP `00`。
  1.0 模式 (送過 GET_CAPS v1.0 後): CAPS_RESP `0b 01 00 03 03 00 80 01 00` = 1.0 版、8k|16k、HTT、幀 128。
                   按住 → AUDIO_START `04 03 02 id` (reason HTT, codec 16 kHz)，128-byte 無表頭幀，
                   每 20 幀送 AUDIO_SYNC `0a codec seq(2) pred(2) step(1)`；放開 → AUDIO_STOP `00 02` 立刻停。
"""
import argparse
import asyncio
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

from audio import CODEC_RATE, StreamDecoder
from probe import open_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ATVV_TX = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
ATVV_RX = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
ATVV_CTL = "ab5e0004-5a21-4f05-bc7d-af01f617b664"

# 主機 → 遙控器 (TX)。來源: Google ATVV 規範 1.0 (經 ATVVoice / onn-remote-linux 整理)
CMD_GET_CAPS_V04 = bytes([0x0A, 0x00, 0x04, 0x00, 0x01])         # version 0.4, codec: 8 kHz ADPCM
CMD_GET_CAPS_V10 = bytes([0x0A, 0x01, 0x00, 0x00, 0x03, 0x03])   # version 1.0, reserved, codecs 8k|16k, 互動模式 PTT|HTT
CMD_MIC_OPEN = bytes([0x0C, 0x00, 0x01])                         # 開麥, codec 8 kHz ADPCM (v0.4 格式)
CMD_MIC_CLOSE = bytes([0x0D, 0xFF])                              # v1.0: 0D <stream id>, 0xFF = 任一串流
CMD_MIC_EXTEND = bytes([0x0E, 0xFF])                             # v1.0: 0E <stream id>

CTL_NAMES = {0x00: "AUDIO_STOP", 0x04: "AUDIO_START", 0x07: "(廠商自訂 07)", 0x08: "START_SEARCH",
             0x0A: "AUDIO_SYNC", 0x0B: "CAPS_RESP", 0x0C: "MIC_OPEN_ERROR"}
START_REASON = {0x00: "MIC_OPEN", 0x01: "PTT", 0x03: "HTT"}
STOP_REASON = {0x00: "MIC_CLOSE", 0x02: "HTT 放開", 0x04: "即將有新串流", 0x08: "傳輸逾時", 0x10: "通知被關閉"}


async def main(args):
    outdir = Path(__file__).parent / "captures"
    outdir.mkdir(exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    log = (outdir / f"atvv-{stamp}.log").open("w", encoding="utf-8")
    start = time.monotonic()
    sdec = StreamDecoder()
    state = {"frames": 0, "first": None, "last": None, "stream": None, "client": None,
             "stream_id": 0xFF, "ended": None}

    def emit(msg, quiet=False):
        line = f"{time.monotonic() - start:8.3f}  {msg}"
        if not quiet:
            print(line)
        log.write(line + "\n")
        log.flush()

    async def tx(data: bytes, why: str):
        try:
            await state["client"].write_gatt_char(ATVV_TX, data, response=False)
            emit(f"TX  -> {data.hex(' ')}   ({why})")
        except Exception as e:  # noqa: BLE001
            emit(f"TX  -> {data.hex(' ')} 失敗: {e}")

    def end_stream_if_needed(now):
        if state["last"] is not None and state["frames"] and now - state["last"] > 0.5 \
                and state["ended"] != state["first"]:
            emit(f"    [串流結束] id={state['stream']} 幀數={state['frames']} "
                 f"長度={(state['last'] - state['first']):.1f}s")
            state["ended"] = state["first"]

    def on_ctl(_s, data: bytearray):
        data = bytes(data)
        name = CTL_NAMES.get(data[0], "?") if data else "?"
        emit(f"CTL <- {data.hex(' ')}   {name}")
        if not data:
            return
        if data[0] == 0x04 and len(data) >= 4:
            emit(f"      AUDIO_START: reason={START_REASON.get(data[1], hex(data[1]))} "
                 f"codec={data[2]:#x} ({CODEC_RATE.get(data[2], '?')} Hz) stream_id={data[3]}")
            state["stream_id"] = data[3]
            sdec.on_start(data[2])
            if args.close_after is not None:
                asyncio.get_event_loop().create_task(close_later(args.close_after))
        elif data[0] == 0x00:
            emit(f"      AUDIO_STOP: reason={STOP_REASON.get(data[1], hex(data[1])) if len(data) > 1 else '(0.4 格式, 無 reason)'}")
        elif data[0] == 0x0A:
            sdec.on_sync(data)
        elif data[0] == 0x0B:
            if data[1:3] == b"\x01\x00":
                model = {0x00: "On-request", 0x01: "PTT", 0x03: "HTT"}.get(data[4], hex(data[4]))
                emit(f"      CAPS_RESP v1.0: codecs={data[3]:#04x} 互動模式={model} "
                     f"幀大小={int.from_bytes(data[5:7], 'big')} 其餘={data[7:].hex(' ')}")
            else:
                emit(f"      CAPS_RESP v0.4: version={data[1:3].hex()} codecs={data[3:5].hex()} "
                     f"幀大小={int.from_bytes(data[5:7], 'big')} 每筆={int.from_bytes(data[7:9], 'big')}")
        elif data[0] == 0x0C:
            emit(f"      MIC_OPEN_ERROR code={data[1:3].hex()}")

    async def close_later(sec):
        await asyncio.sleep(sec)
        await tx(bytes([0x0D, state["stream_id"]]), f"AUDIO_START 後 {sec}s 送 MIC_CLOSE")

    def on_audio(_s, data: bytearray):
        data = bytes(data)
        now = time.monotonic()
        emit(f"RX  {data.hex(' ')}", quiet=True)
        if state["last"] is None or now - state["last"] > 0.5:
            end_stream_if_needed(now)
            state["frames"], state["first"] = 0, now
            state["stream"] = data[2] if len(data) == 134 else state["stream_id"]
            emit(f"    [串流開始] id={state['stream']} 幀長={len(data)} "
                 f"{'(0.4 有表頭)' if len(data) == 134 else '(1.0 無表頭)'} 前8bytes={data[:8].hex(' ')}")
        state["frames"] += 1
        state["last"] = now
        if state["frames"] % 50 == 0:
            emit(f"    … {state['frames']} 幀 ({now - state['first']:.1f}s)")
        if args.wav:
            sdec.on_frame(data)

    async with open_client("PlayRemote") as client:
        state["client"] = client
        await client.start_notify(ATVV_CTL, on_ctl)
        await client.start_notify(ATVV_RX, on_audio)
        emit(f"已連線 {client.address}，紀錄: captures/atvv-{stamp}.log")
        if args.send:
            await tx(bytes.fromhex(args.send), "--send")
        if args.caps:
            await tx(CMD_GET_CAPS_V04, "GET_CAPS v0.4")
        if args.caps_v1:
            await tx(CMD_GET_CAPS_V10, "GET_CAPS v1.0")
        if args.mic_open:
            await asyncio.sleep(1)
            await tx(CMD_MIC_OPEN, "主機主動 MIC_OPEN")
        emit("現在請操作語音鍵 (依指示按一下 / 按住)。Ctrl+C 結束。")
        last_extend = 0.0
        try:
            while client.is_connected:
                await asyncio.sleep(0.2)
                now = time.monotonic()
                end_stream_if_needed(now)
                streaming = state["last"] is not None and now - state["last"] < 0.5
                if args.extend and streaming and now - last_extend >= args.extend:
                    await tx(bytes([0x0E, state["stream_id"]]), f"每 {args.extend}s MIC_EXTEND")
                    last_extend = now
        except asyncio.CancelledError:
            pass

    if args.wav and sdec.pcm:
        out = outdir / f"atvv-{stamp}.wav"
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sdec.rate)
            w.writeframes(bytes(sdec.pcm))
        emit(f"已存 {out} ({len(sdec.pcm) // 2 / sdec.rate:.1f}s @ {sdec.rate} Hz)")
    log.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--caps", action="store_true")
    p.add_argument("--caps-v1", action="store_true")
    p.add_argument("--mic-open", action="store_true")
    p.add_argument("--close-after", type=float, default=None)
    p.add_argument("--extend", type=float, default=None)
    p.add_argument("--send", default=None, help="hex 字串，連線後寫入 TX")
    p.add_argument("--wav", action="store_true")
    a = p.parse_args()
    try:
        asyncio.run(main(a))
    except KeyboardInterrupt:
        print("\n結束。")
