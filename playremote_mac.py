#!/usr/bin/env python3
"""PlayRemote 驅動程式 — macOS 版（USB 接收器模式）。

功能:
- 攔接收器的 HID 報告，每個鍵可設單擊 / 雙擊 / 長按動作（啟動程式、送組合鍵、打字），與 Windows 版共用設定格式。
- block=true 的鍵不會再傳給系統；沒設定的鍵照常。
- 語音鍵: hotkey 模式送熱鍵給語音輸入 App (例如 SogaType 的 Fn)，麥克風直接用系統裡的「PlayRemote」；
          builtin 模式自己從 PlayRemote 麥克風錄音、放開後離線辨識、把文字打進目前視窗。

用法:
  playremote_mac.py [--config config.toml] [-v]

需要的權限 (系統設定 → 隱私權與安全性):
  輸入監控 (讀 HID)、輔助使用 (送按鍵)、麥克風 (builtin 模式)。授權對象是執行它的終端機或 App。
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import hid_mac
import macio
from gestures import GESTURE_NAME, GestureDetector, normalize_keys

HERE = Path(__file__).resolve().parent
LOG_HANDLERS: list = []


def log(msg: str):
    line = f"{datetime.now():%H:%M:%S}  {msg}"
    print(line, flush=True)
    for h in list(LOG_HANDLERS):
        try:
            h(line)
        except Exception:  # noqa: BLE001
            pass


class Recorder:
    """builtin 語音模式：按住時從指定輸入裝置錄音。"""

    def __init__(self, device_hint: str):
        import numpy as np  # noqa: F401
        import sounddevice as sd
        self.sd = sd
        self.idx = None
        self.rate = 16000
        for i, d in enumerate(sd.query_devices()):
            if device_hint.lower() in d["name"].lower() and d["max_input_channels"] > 0:
                self.idx, self.rate = i, int(d["default_samplerate"])
                break
        self.stream = None
        self.chunks: list[bytes] = []

    @property
    def available(self) -> bool:
        return self.idx is not None

    def start(self):
        self.chunks = []

        def cb(indata, frames, _t, status):
            self.chunks.append(bytes(indata))

        self.stream = self.sd.InputStream(device=self.idx, samplerate=self.rate, channels=1, dtype="int16",
                                          callback=cb)
        self.stream.start()

    def stop(self) -> tuple[bytes, int]:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        return b"".join(self.chunks), self.rate


class Driver:
    def __init__(self, cfg: dict, verbose: bool = False, on_status=None, on_key=None):
        self.cfg = cfg
        self.verbose = verbose
        self.on_status = on_status
        self.on_key = on_key
        inp = cfg.get("input", {})
        self.keys = normalize_keys(cfg.get("keys", {})) if inp.get("enabled", True) else {}
        self.voice: dict = cfg.get("voice", {})
        self.stt: dict = self.voice.get("stt", {})
        self.voice_key = self.voice.get("usb_key", "search")
        self.captures = HERE / "captures"
        self.captures.mkdir(exist_ok=True)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.gd: Optional[GestureDetector] = None
        self.voice_combo: Optional[macio.Combo] = None
        self.voice_down = False
        self.recorder: Optional[Recorder] = None
        self._stop = False

        # 哪些介面要獨占：該介面裡有任一鍵 block=true，或語音鍵在該介面 (避免 search 鍵被系統當成 Spotlight)
        seize = set()
        for iface, names in hid_mac.KEYS_OF_IFACE.items():
            if any(self.keys.get(n, {}).get("block") for n in names) or self.voice_key in names:
                seize.add(iface)
        self.listener = hid_mac.HidListener(
            self.on_remote_event, seize_ifaces=seize, on_log=lambda m: log(f"  hid: {m}"),
            on_raw=(lambda iface, raw: log(f"  raw {iface}: {raw.hex(' ')}")) if verbose else None)

        if self.voice.get("mode", "hotkey") == "builtin":
            try:
                self.recorder = Recorder(self.voice.get("mic", "PlayRemote"))
                if not self.recorder.available:
                    log(f"找不到輸入裝置「{self.voice.get('mic', 'PlayRemote')}」，builtin 語音不可用 (接收器插了嗎？)")
                    self.recorder = None
                else:
                    log(f"builtin 語音: 錄音裝置 {self.recorder.sd.query_devices(self.recorder.idx)['name']} "
                        f"{self.recorder.rate} Hz")
            except Exception as e:  # noqa: BLE001
                log(f"builtin 語音初始化失敗: {e}")

    # ---------------- 按鍵 ----------------
    def start_input(self):
        self.listener.start()
        log(f"HID 監看啟動。獨占介面: {sorted(self.listener.seize) or '無'}；語音鍵: {self.voice_key}")

    def on_remote_event(self, ev: hid_mac.RemoteEvent):
        if ev.name == self.voice_key and ev.kind == "key":
            if ev.down and not self.voice_down:
                self.voice_down = True
                self.on_voice_press()
            elif not ev.down and self.voice_down:
                self.voice_down = False
                self.on_voice_release()
            return
        entry = self.keys.get(ev.name)
        if self.verbose or (ev.kind == "key" and ev.down) or ev.kind == "wheel":
            log(f"鍵 {ev.name} {'按下' if ev.down else '放開'}" + ("" if entry else " (未設定，照常傳給系統)"))
        if not entry or not entry["block"]:
            if ev.seized and ev.kind == "key":
                self._replay(ev.name, ev.down)      # 介面被獨占但這個鍵沒要擋 → 補送回系統
        if not entry:
            return
        if ev.kind == "wheel":
            self.gd.instant(ev.name, entry)
        elif ev.down:
            self.gd.down(ev.name, entry)
        else:
            self.gd.up(ev.name, entry)

    def _replay(self, name: str, down: bool):
        key = hid_mac.REPLAY_KEYS.get(name)
        if not key:
            return
        try:
            c = macio.parse_combo(key)
            macio.key_down(c) if down else macio.key_up(c)
        except Exception as e:  # noqa: BLE001
            log(f"  補送 {name} 失敗: {e}")

    def fire(self, name: str, entry: dict, gesture: str):
        action = entry.get(gesture)
        label = GESTURE_NAME[gesture]
        if self.on_key:
            try:
                self.on_key(name, gesture)
            except Exception:  # noqa: BLE001
                pass
        if not action:
            log(f"  {name} {label}: 未設定動作" + ("，已擋掉" if entry["block"] else ""))
            return
        try:
            if "run" in action:
                log(f"  {name} {label} → 執行: {action['run']}")
                macio.run(action["run"])
            elif "keys" in action:
                log(f"  {name} {label} → 送鍵: {action['keys']}")
                macio.send_keys(action["keys"])
            elif "text" in action:
                log(f"  {name} {label} → 打字: {action['text']!r}")
                macio.type_text(action["text"])
        except Exception as e:  # noqa: BLE001
            log(f"  動作 {name} {label} 失敗: {e}")

    # ---------------- 語音 ----------------
    def on_voice_press(self):
        mode = self.voice.get("mode", "hotkey")
        log(f"語音鍵按下 ({mode})")
        if mode == "hotkey":
            combo = self.voice.get("hotkey", "fn")
            style = self.voice.get("hotkey_style", "hold")
            try:
                self.voice_combo = macio.parse_combo(combo)
            except ValueError as e:
                log(f"  hotkey 設定錯誤: {e}")
                return
            if style == "hold":
                macio.key_down(self.voice_combo)
            elif style == "double":
                macio.send_keys(combo)
                time.sleep(0.08)
                macio.send_keys(combo)
            else:
                macio.send_keys(combo)
        elif mode == "builtin" and self.recorder:
            try:
                self.recorder.start()
            except Exception as e:  # noqa: BLE001
                log(f"  錄音失敗: {e}")

    def on_voice_release(self):
        mode = self.voice.get("mode", "hotkey")
        log("語音鍵放開")
        if mode == "hotkey" and self.voice_combo:
            style = self.voice.get("hotkey_style", "hold")
            if style == "hold":
                macio.key_up(self.voice_combo)
            elif style == "tap":
                macio.send_keys(self.voice.get("hotkey", "fn"))
            self.voice_combo = None
        elif mode == "builtin" and self.recorder:
            pcm, rate = self.recorder.stop()
            self.on_utterance(pcm, rate)

    def on_utterance(self, pcm: bytes, rate: int):
        secs = len(pcm) / 2 / rate
        if secs < float(self.voice.get("min_seconds", 0.3)):
            log(f"語音太短 ({secs:.1f}s)，忽略")
            return
        out = self.captures / f"voice-{datetime.now():%Y%m%d-%H%M%S}.wav"
        with wave.open(str(out), "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(2)
            wv.setframerate(rate)
            wv.writeframes(pcm)
        log(f"錄音 {secs:.1f}s → {out.name}")
        if self.loop:
            self.loop.create_task(self.transcribe(out))

    async def transcribe(self, wav: Path):
        t0 = time.monotonic()
        cmd = self.stt.get("command", "")
        try:
            if cmd:
                cmd = cmd.replace("{wav}", str(wav))
                log(f"辨識中: {cmd}")
                proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE,
                                                             stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    log(f"辨識指令失敗 (code {proc.returncode}): {stderr.decode('utf-8', 'replace')[:300]}")
                    return
                text = stdout.decode("utf-8", "replace").strip()
            else:
                import stt
                lang = self.stt.get("lang", "zh-TW")
                log(f"辨識中 (macOS 內建, {lang})")
                text = await asyncio.get_event_loop().run_in_executor(None, stt.transcribe_file, str(wav), lang)
        except Exception as e:  # noqa: BLE001
            log(f"辨識失敗: {e}")
            return
        log(f"辨識結果 ({time.monotonic() - t0:.1f}s): {text!r}")
        if text and self.stt.get("type_result", True):
            macio.type_text(text)
            if self.stt.get("append"):
                macio.send_keys(self.stt["append"])
        if not self.voice.get("save_wav", False):
            wav.unlink(missing_ok=True)

    # ---------------- 主迴圈 ----------------
    async def run(self):
        self.loop = asyncio.get_event_loop()
        inp = self.cfg.get("input", {})
        self.gd = GestureDetector(self.loop, self.fire, inp.get("double_click_ms", 600), inp.get("long_press_ms", 500))
        self.start_input()
        last_n = -1
        while not self._stop:
            self.listener.pump(0.02)          # HID 回呼在這裡發生
            n = len(self.listener.devices)
            if n != last_n:
                log(f"接收器 HID 介面: {n} 個" + ("" if n else "（請插上 PlayRemote USB 接收器）"))
                last_n = n
                if self.on_status:
                    self.on_status(n > 0, None)
            await asyncio.sleep(0.005)

    def request_stop(self):
        self._stop = True


def load_config(path: Path) -> dict:
    if not path.exists():
        example = HERE / "config.example.mac.toml"
        shutil.copy(example, path)
        log(f"沒有 {path.name}，已從 {example.name} 建立，請依需求修改。")
    with open(path, "rb") as f:
        return tomllib.load(f)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--config", default=str(HERE / "config.toml"))
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("這是 macOS 版；Windows 請用 playremote.py")
    cfg = load_config(Path(a.config))
    d = Driver(cfg, verbose=a.verbose)
    try:
        asyncio.run(d.run())
    except KeyboardInterrupt:
        print("\n結束。")


if __name__ == "__main__":
    main()
