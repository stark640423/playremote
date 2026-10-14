#!/usr/bin/env python3
"""測試 PlayRemote USB 接收器的麥克風：錄 12 秒並逐秒印音量，存成 WAV。

用法:
  usb_mic_test.py [--device PlayRemote] [--seconds 12]

請在錄音期間：前 4 秒不要按語音鍵、中間 4 秒按住語音鍵說話、最後 4 秒放開。
藉此判斷接收器是「一直送聲音」還是「只在按住語音鍵時送聲音」。
需要「麥克風」權限（第一次執行時系統會詢問）。
"""
import argparse
import math
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--device", default="PlayRemote")
    p.add_argument("--seconds", type=int, default=12)
    a = p.parse_args()

    idx = next((i for i, d in enumerate(sd.query_devices())
                if a.device.lower() in d["name"].lower() and d["max_input_channels"] > 0), None)
    if idx is None:
        print(f"找不到輸入裝置「{a.device}」。目前輸入裝置：")
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"  [{i}] {d['name']}")
        return
    info = sd.query_devices(idx)
    rate = int(info["default_samplerate"])
    print(f"錄音裝置: {info['name']}  {rate} Hz, {a.seconds} 秒。\n"
          "  0-4 秒：不要按語音鍵\n  4-8 秒：按住語音鍵說話\n  8-12 秒：放開\n")
    chunks = []
    with sd.InputStream(device=idx, samplerate=rate, channels=1, dtype="int16") as st:
        for sec in range(a.seconds):
            data, _ = st.read(rate)
            chunks.append(data.copy())
            x = data.astype(np.float32) / 32768.0
            rms = math.sqrt(float(np.mean(x * x)) + 1e-12)
            bar = "#" * min(50, int(rms * 400))
            print(f"  {sec+1:2d}s  rms={rms:.4f} peak={float(np.abs(x).max()):.3f}  {bar}")
    pcm = np.concatenate(chunks)
    out = Path(__file__).parent / "captures"
    out.mkdir(exist_ok=True)
    path = out / f"usbmic-{datetime.now():%Y%m%d-%H%M%S}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm.tobytes())
    print(f"\n已存檔: {path}")


if __name__ == "__main__":
    main()
