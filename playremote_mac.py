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

import coreaudio_mac
import hid_mac
import macio
from gestures import GESTURE_NAME, GestureDetector, normalize_keys

HERE = Path(__file__).resolve().parent
# 打包成 .app 後，設定 / 記錄 / 錄音放到使用者的 Application Support，不要寫進 app 內部
DATA_DIR = (Path.home() / "Library" / "Application Support" / "PlayRemote") if getattr(sys, "frozen", False) else HERE
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_HANDLERS: list = []
LOCK_PATH = Path.home() / "Library" / "Application Support" / "PlayRemote" / ".driver.lock"


def acquire_single_instance():
    """同一時間只能有一個驅動在跑（App 或命令列都一樣），否則會互搶遙控器。回傳鎖檔物件，拿不到回傳 None。"""
    import fcntl
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_PATH, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(str(__import__("os").getpid()))
    f.flush()
    return f


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
        self.captures = DATA_DIR / "captures"
        self.captures.mkdir(exist_ok=True)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.gd: Optional[GestureDetector] = None
        self.voice_combo: Optional[macio.Combo] = None
        self.voice_down = False
        self.recorder: Optional[Recorder] = None
        self._stop = False
        # 連接方式：usb＝2.4G 接收器 (VID 0x0610)，ble＝系統藍牙配對 (VID 0x2D01)
        self.transport = str(cfg.get("remote", {}).get("transport", "usb")).lower()
        if self.transport not in ("usb", "ble"):
            self.transport = "usb"
        vid, pid = (hid_mac.BLE_VID, hid_mac.BLE_PID) if self.transport == "ble" else (0x0610, 0x1000)

        # 自動切麥克風只用於 USB 模式（藍牙沒有系統麥克風可切，語音走 ATVV）
        self.mic_switch: Optional[coreaudio_mac.MicSwitcher] = None
        if self.transport == "usb" and self.voice.get("auto_mic", True) and self.voice.get("mode", "hotkey") == "hotkey":
            sw = coreaudio_mac.MicSwitcher(self.voice.get("mic", "PlayRemote"))
            self.mic_switch = sw
            log("語音鍵按下時會把系統預設麥克風切成「PlayRemote」" + ("" if sw.available else "（目前找不到，插上接收器後生效）"))

        # 哪些介面要獨占：該介面裡有任一鍵 block=true，或語音鍵在該介面 (避免 search 鍵被系統當成 Spotlight)
        seize = set()
        for iface, names in hid_mac.KEYS_OF_IFACE.items():
            if any(self.keys.get(n, {}).get("block") for n in names) or self.voice_key in names:
                seize.add(iface)
        self.listener = hid_mac.HidListener(
            self.on_remote_event, seize_ifaces=seize, vid=vid, pid=pid, transport=self.transport,
            on_log=lambda m: log(f"  hid: {m}"),
            on_raw=(lambda iface, raw: log(f"  raw {iface}: {raw.hex(' ')}")) if verbose else None)

        if self.transport == "usb" and self.voice.get("mode", "hotkey") == "builtin":
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
        if not hid_mac.input_monitoring_granted():
            # 只觸發系統的授權提示（第一次才會跳）。是否真的沒權限，以實際開啟遙控器的結果為準：
            # IOHIDCheckAccess 在剛授權或簽章版本更換後常回「未知」，直接提示會誤報。
            hid_mac.request_input_monitoring()
        self.listener.start()
        log(f"HID 監看啟動。獨占介面: {sorted(self.listener.seize) or '無'}；語音鍵: {self.voice_key}")

    def on_remote_event(self, ev: hid_mac.RemoteEvent):
        if ev.kind == "motion":
            # 藍牙獨占模式：陀螺儀位移系統收不到，補送滑鼠移動
            try:
                macio.mouse_move(ev.dx, ev.dy)
            except Exception as e:  # noqa: BLE001
                log(f"  補送滑鼠移動失敗: {e}")
            return
        if self.on_key and (ev.down or ev.kind == "wheel"):
            try:
                self.on_key(ev.name, "press")       # 介面亮燈：每次按下都通知，不等手勢判定
            except Exception:  # noqa: BLE001
                pass
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
            elif ev.seized and ev.kind == "wheel":
                try:
                    macio.scroll(1 if ev.name == "wheel_up" else -1)
                except Exception as e:  # noqa: BLE001
                    log(f"  補送滾輪失敗: {e}")
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
            if self.mic_switch:
                try:
                    before = coreaudio_mac.device_name(coreaudio_mac.get_default_input())
                    if self.mic_switch.switch():
                        now = coreaudio_mac.device_name(coreaudio_mac.get_default_input())
                        log(f"  麥克風：{before} → {now}")
                        time.sleep(0.08)           # 讓 App 收到熱鍵時已經是新的預設麥克風
                    else:
                        log("  找不到 PlayRemote 麥克風，維持原本的麥克風")
                except Exception as e:  # noqa: BLE001
                    log(f"  切換麥克風失敗: {e}")
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
        if mode == "hotkey" and self.mic_switch and self.loop:
            self.loop.call_later(0.8, self._restore_mic)   # 等 App 停止錄音再切回
        elif mode == "builtin" and self.recorder:
            pcm, rate = self.recorder.stop()
            self.on_utterance(pcm, rate)

    def _restore_mic(self):
        try:
            self.mic_switch.restore()
            log(f"  麥克風切回：{coreaudio_mac.device_name(coreaudio_mac.get_default_input())}")
        except Exception as e:  # noqa: BLE001
            log(f"  切回麥克風失敗: {e}")

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

    # ---------------- 藍牙語音 (ATVV over GATT) ----------------
    def _atvv_thread_main(self):
        """獨立執行緒入口：用自己的 event loop 跑 bleak（與 HID 的 run loop 隔離）。

        注意：沒有 NSBluetoothAlwaysUsageDescription 的裸 Python 一碰藍牙會被 macOS 直接 SIGABRT，
        所以先檢查藍牙授權狀態，未授權就不碰 CoreBluetooth（打包成 .app 有這個 key，不受影響）。
        """
        auth = self._bluetooth_authorization()
        if auth == "denied":
            log("藍牙語音：沒有藍牙權限，語音功能停用（到系統設定→隱私權→藍牙 開啟 PlayRemote 再重開）")
            return
        # notDetermined 時，CoreBluetooth 會想跳權限視窗，需要 Info.plist 的 NSBluetoothAlwaysUsageDescription，
        # 否則整個程序會被 macOS 直接 SIGABRT。打包成 .app 有這個 key；從原始碼裸跑且尚未授權時先略過，避免整個 app 崩掉。
        if auth == "unknown" and not getattr(sys, "frozen", False):
            log("藍牙語音：藍牙權限未定且非打包版，先略過語音（請用打包好的 PlayRemote.app，或先在系統設定授權藍牙）")
            return
        try:
            asyncio.run(self._atvv_loop())
        except Exception as e:  # noqa: BLE001
            log(f"藍牙語音執行緒結束：{type(e).__name__}: {e}")

    @staticmethod
    def _bluetooth_authorization() -> str:
        """回傳 'ok' / 'denied' / 'unknown'（notDetermined 當 unknown，讓 .app 去跳權限視窗）。"""
        try:
            from CoreBluetooth import CBCentralManager
            v = int(CBCentralManager.authorization())
            return {0: "unknown", 1: "denied", 2: "denied", 3: "ok"}.get(v, "unknown")
        except Exception:  # noqa: BLE001
            return "unknown"

    async def _atvv_loop(self):
        """訂閱 ATVV：遙控器按住麥克風鍵 → 收串流 → 解碼 → 離線辨識 → 打進目前視窗。斷線自動重連。"""
        import audio
        import probe
        name = self.cfg.get("remote", {}).get("name", "PlayRemote")
        log("藍牙語音：背景連線 ATVV 中（遙控器要醒著並保持在系統藍牙連線）")
        while not self._stop:
            try:
                async with probe.open_client(name) as client:
                    mic = audio.AtvvMic(
                        client, on_utterance=self._atvv_transcribe_sync,
                        on_press=self._atvv_press, on_release=self._atvv_release,
                        log=lambda m: log(f"  atvv: {m}"))
                    await mic.start(negotiate_v10=True)
                    log("藍牙語音已就緒：按住遙控器麥克風鍵說話，放開後會離線辨識並打字。")
                    while client.is_connected and not self._stop:
                        await asyncio.sleep(0.1)
                        await mic.tick()
            except Exception as e:  # noqa: BLE001
                log(f"藍牙語音連線中斷：{type(e).__name__}: {e}")
            if self._stop:
                break
            await asyncio.sleep(3)          # 遙控器睡眠/斷線後，等一下再重連

    def _atvv_transcribe_sync(self, pcm: bytes, rate: int):
        """在 ATVV 執行緒內同步處理一段語音：存檔→辨識→打字（不碰 HID 的 event loop）。"""
        try:
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
            log(f"錄音 {secs:.1f}s → {out.name}，辨識中…")
            cmd = self.stt.get("command", "")
            if cmd:
                import subprocess
                r = subprocess.run(cmd.replace("{wav}", str(out)), shell=True,
                                   capture_output=True, text=True)
                text = r.stdout.strip() if r.returncode == 0 else ""
                if r.returncode != 0:
                    log(f"辨識指令失敗 (code {r.returncode}): {r.stderr[:200]}")
            else:
                import stt
                text = stt.transcribe_file(str(out), self.stt.get("lang", "zh-TW"))
            log(f"辨識結果: {text!r}")
            if text and self.stt.get("type_result", True):
                macio.type_text(text)
                if self.stt.get("append"):
                    macio.send_keys(self.stt["append"])
            if not self.voice.get("save_wav", False):
                out.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            log(f"藍牙語音處理失敗：{type(e).__name__}: {e}")

    def _atvv_press(self):
        log("語音鍵按下（藍牙 ATVV）")
        if self.on_key:
            try:
                self.on_key(self.voice_key, "press")     # GUI 亮燈
            except Exception:  # noqa: BLE001
                pass

    def _atvv_release(self):
        log("語音鍵放開（藍牙 ATVV）")

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
        self._lock = acquire_single_instance()
        if self._lock is None:
            log("⚠ 已經有另一個 PlayRemote 驅動在執行（App 或終端機裡的 playremote_mac.py），請先關掉它")
            if self.on_status:
                self.on_status(False, None)
            raise RuntimeError("已經有另一個 PlayRemote 驅動在執行，請先關掉它")
        try:
            await self._run_locked()
        finally:
            self.listener.stop()
            try:
                self._lock.close()                 # 釋放單一實例鎖（App 內重新啟動驅動時才拿得到）
            except Exception:  # noqa: BLE001
                pass
            self._lock = None

    async def _run_locked(self):
        self.loop = asyncio.get_event_loop()
        inp = self.cfg.get("input", {})
        self.gd = GestureDetector(self.loop, self.fire, inp.get("double_click_ms", 600), inp.get("long_press_ms", 500))
        self.start_input()
        # 藍牙模式：語音鍵不送 HID，語音改走 ATVV GATT。放在獨立執行緒跑 bleak（自己的 event loop），
        # 與 HID 的 run loop 完全隔離——這樣就算藍牙連線出問題，也絕不會影響按鍵。
        self._atvv_thread = None
        if self.transport == "ble" and self.voice.get("mode", "hotkey") != "none":
            import threading
            self._atvv_thread = threading.Thread(target=self._atvv_thread_main, name="atvv", daemon=True)
            self._atvv_thread.start()
        last_n = -1
        while not self._stop:
            self.listener.pump(0.02)          # HID 回呼在這裡發生
            n = len(self.listener.devices)
            if n != last_n:
                if self.transport == "ble":
                    hint = "（請確認 PlayRemote 已在系統藍牙配對並連線）"
                else:
                    hint = "（請插上 PlayRemote USB 接收器）"
                log(f"遙控器 HID 介面: {n} 個" + ("" if n else hint))
                if "permission" in self.listener.problems:
                    log("⚠ 讀不到遙控器按鍵（沒有「輸入監控」權限）。已打開設定頁：若清單裡已有 PlayRemote，"
                        "請先選它按「−」移除，再按「+」從「應用程式」加回 PlayRemote 並打開，然後重開 App。"
                        "（更新或換簽章後，舊的授權項目會失效）")
                    import subprocess
                    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"])
                if "exclusive" in self.listener.problems:
                    log("⚠ 遙控器被另一個程式佔用：請關掉其他 PlayRemote 或 playremote_mac.py")
                if "no_seize" in self.listener.problems and n:
                    log("ℹ 藍牙模式無法擋鍵（macOS 不讓第三方獨占已配對鍵盤）：自訂動作會觸發，但原本的按鍵也會照送，"
                        "例如按 1 會開 Claude 同時也打出 1。")
                last_n = n
                if self.on_status:
                    self.on_status(n > 0, None)
            await asyncio.sleep(0.005)
        if getattr(self, "_atvv_thread", None):
            self._atvv_thread.join(timeout=2.0)         # 執行緒看到 self._stop 會自己收尾
        self.listener.stop()

    def request_stop(self):
        self._stop = True


def run_blocking(driver: "Driver"):
    """在目前執行緒跑驅動直到 request_stop()（gui.py 的背景執行緒用）。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(driver.run())
    finally:
        driver.listener.stop()
        loop.close()


def load_config(path: Path) -> dict:
    if not path.exists():
        example = Path(getattr(sys, "_MEIPASS", HERE)) / "config.example.mac.toml"
        shutil.copy(example, path)
        log(f"沒有 {path.name}，已從 {example.name} 建立，請依需求修改。")
    with open(path, "rb") as f:
        return tomllib.load(f)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--config", default=str(DATA_DIR / "config.toml"))
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
