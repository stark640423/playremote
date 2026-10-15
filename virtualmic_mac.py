"""macOS：把 PCM 串流即時灌進虛擬音訊裝置（BlackHole），當成麥克風餵給語音輸入 App（例如 SogaType）。

藍牙模式沒有實體麥克風，語音是 ATVV 傳來的 PCM。流程：
  遙控器 →(ATVV)→ 本模組播放到 BlackHole 輸出 →(BlackHole 迴路)→ BlackHole 輸入 →(系統預設麥克風)→ SogaType

需要先安裝 BlackHole（免費開源虛擬音訊裝置）。Windows 版對應的是 VB-CABLE（virtualmic.py）。
"""
from __future__ import annotations

from typing import Optional


class VirtualMic:
    """把 16-bit mono PCM 即時播放到虛擬輸出裝置（會自動重取樣成裝置取樣率）。"""

    def __init__(self, device_hint: str = "BlackHole", src_rate: int = 16000):
        import numpy as np  # noqa: F401
        import sounddevice as sd
        self.sd = sd
        self.np = np
        self.device_hint = device_hint
        self.idx: Optional[int] = None
        self.target_rate = 48000
        self.channels = 2
        for i, d in enumerate(sd.query_devices()):
            if device_hint.lower() in d["name"].lower() and d["max_output_channels"] > 0:
                self.idx = i
                self.target_rate = int(d["default_samplerate"]) or 48000
                self.channels = 2 if d["max_output_channels"] >= 2 else 1
                break
        self.src_rate = src_rate
        self.stream = None

    @property
    def available(self) -> bool:
        return self.idx is not None

    @property
    def name(self) -> str:
        return self.sd.query_devices(self.idx)["name"] if self.idx is not None else ""

    def start(self, src_rate: Optional[int] = None):
        if src_rate:
            self.src_rate = src_rate
        # 緩衝要夠大才吃得下藍牙音訊的抖動（太小會掉幀、收不全）。用預設（偏保守但穩）。
        self.stream = self.sd.OutputStream(device=self.idx, samplerate=self.target_rate,
                                           channels=self.channels, dtype="float32")
        self.stream.start()

    def feed(self, pcm_bytes: bytes):
        """寫入一段 16-bit mono PCM（src_rate）；需要時重取樣到裝置取樣率並擴成多聲道。"""
        if not self.stream or not pcm_bytes:
            return
        np = self.np
        x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if self.src_rate != self.target_rate and len(x) > 0:
            factor = self.target_rate / self.src_rate
            if abs(factor - round(factor)) < 1e-6:
                x = np.repeat(x, int(round(factor)))        # 整數倍（16k→48k=3x）：直接重複，跨幀連續、無邊界雜訊
            else:
                n_out = int(round(len(x) * factor))
                xi = np.linspace(0, len(x) - 1, n_out)
                x = np.interp(xi, np.arange(len(x)), x).astype(np.float32)
        if self.channels == 2:
            x = np.column_stack([x, x])
        try:
            self.stream.write(x)
        except Exception:  # noqa: BLE001
            pass

    def stop(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:  # noqa: BLE001
                pass
            self.stream = None
