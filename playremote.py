#!/usr/bin/env python3
"""PlayRemote Windows 驅動雛形 (常駐程式)。

  playremote.py [--config config.toml] [--verbose]

做的事:
  1. 自動連上遙控器 (bleak，直接用 MAC)，斷線自動重連，讀電量。
  2. 用 Raw Input 只收 PlayRemote 的按鍵，每個鍵可設單擊 / 雙擊 / 長按三種動作
     (啟動程式 / 送組合鍵 / 打字)；設 block = true 的鍵用低階鍵盤 hook 擋掉，不會再當一般鍵盤輸入。
  3. 語音鍵按住說話 (ATVV 1.0)，放開後依 [voice] 設定: 跑外部辨識指令把文字打進目前視窗、
     或轉成按住某個熱鍵 (可搭配虛擬麥克風讓那個 App 直接錄到遙控器的聲音)、或只存 WAV。

設定檔格式見 config.example.toml。Ctrl+C 結束。
"""
import argparse
import asyncio
import shutil
import sys
import time
import tomllib
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

from bleak import BleakClient
from bleak.backends.device import BLEDevice

import probe
import rawinput
import winio
from audio import AtvvMic

try:
    from virtualmic import VirtualMic
except Exception as _e:  # noqa: BLE001  (沒裝 sounddevice / pycaw 時仍可跑其他功能)
    VirtualMic = None
    _VMIC_IMPORT_ERROR = _e

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 打包成 exe (PyInstaller) 時，設定檔與 captures/ 放在 exe 旁邊；原始碼執行時放在這個檔案旁邊
HERE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
BATTERY_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
GESTURES = ("single", "double", "long")
GESTURE_NAME = {"single": "單擊", "double": "雙擊", "long": "長按"}
ACTION_KEYS = ("run", "keys", "text")


LOG_HANDLERS = []          # GUI 可以註冊 callable(str) 接收訊息


def log(msg: str):
    line = f"{datetime.now():%H:%M:%S}  {msg}"
    print(line, flush=True)
    for h in list(LOG_HANDLERS):
        try:
            h(line)
        except Exception:  # noqa: BLE001
            pass


def normalize_keys(raw: dict) -> dict:
    """把設定檔的按鍵表整理成 {鍵名: {block, single, double, long}}。

    兩種寫法都接受:
      "1" = { run = "notepad", block = true }                      # 只有單擊，直接寫動作
      "1" = { block = true, single = {...}, double = {...}, long = {...} }
    """
    out = {}
    for name, a in raw.items():
        if not isinstance(a, dict):
            continue
        entry = {"block": bool(a.get("block", False)), "single": None, "double": None, "long": None}
        if any(g in a for g in GESTURES):
            for g in GESTURES:
                act = a.get(g)
                entry[g] = act if isinstance(act, dict) and any(k in act for k in ACTION_KEYS) else None
        else:
            act = {k: v for k, v in a.items() if k in ACTION_KEYS}
            entry["single"] = act or None
        out[name] = entry
    return out


class Driver:
    def __init__(self, cfg: dict, verbose: bool = False, on_status=None, on_key=None):
        self.cfg = cfg
        self.verbose = verbose
        self.on_status = on_status      # callable(connected: bool, battery: int | None)
        self.on_key = on_key            # callable(name: str, gesture: str)
        self._stop = False
        self._tasks: list = []
        inp = cfg.get("input", {})
        self.keys = normalize_keys(cfg.get("keys", {})) if inp.get("enabled", True) else {}
        self.double_ms = float(inp.get("double_click_ms", 300))
        self.long_ms = float(inp.get("long_press_ms", 500))
        self.voice: dict = cfg.get("voice", {})
        self.stt: dict = self.voice.get("stt", {})
        self.captures = HERE / "captures"
        self.captures.mkdir(exist_ok=True)
        consumer_names = set(rawinput.KEY_NAMES_BY_CONSUMER.values())
        self.consumer_block = {n for n, e in self.keys.items() if e["block"] and n in consumer_names}
        queued = {rawinput.KEY_NAME_TO_VK[n] for n in self.consumer_block if n in rawinput.KEY_NAME_TO_VK}
        self.blocker = rawinput.KeyBlocker(
            queued,
            on_block=(lambda vk, down: log(f"  hook 擋 vk=0x{vk:02X} {'down' if down else 'up'}")) if verbose else None)
        self.listener = rawinput.RawInputListener(self.on_remote_event,
                                                  on_raw=(lambda label, text: log(f"  raw {label}: {text}")) if verbose else None)
        self.listener.foreign_vks = set(queued)
        self.gstate: dict[str, dict] = {}
        self.recent_consumer: dict[tuple[int, bool], float] = {}   # 剛被吞掉的 Consumer 鍵 (vk, down) → 時間
        self.voice_vks: list[int] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.connected = False
        self.battery: Optional[int] = None
        self.vmic = None
        vm_cfg = self.voice.get("virtual_mic", {})
        if vm_cfg.get("enabled"):
            if VirtualMic is None:
                log(f"虛擬麥克風不可用 (缺套件): {_VMIC_IMPORT_ERROR}")
            else:
                try:
                    vm = VirtualMic(vm_cfg.get("playback", "CABLE Input"), vm_cfg.get("capture", "CABLE Output"),
                                    log=lambda m: log(f"  vmic: {m}"))
                    log(f"虛擬麥克風: {vm.describe()}")
                    if vm.available:
                        self.vmic = vm
                    else:
                        log("虛擬麥克風不可用，語音鍵只會送熱鍵 (請安裝 VB-CABLE，或在 [voice.virtual_mic] 指定正確的裝置名稱)")
                except Exception as e:  # noqa: BLE001
                    log(f"虛擬麥克風初始化失敗，語音鍵只會送熱鍵: {type(e).__name__}: {e}")

    # ---------------- 按鍵 ----------------
    def start_input(self):
        self.listener.start()
        devs = self.listener.devices
        log(f"Raw Input: 找到 {len(devs)} 個 PlayRemote HID 裝置" + ("" if devs else " (遙控器還沒連上 Windows?)"))
        if any(e["block"] for e in self.keys.values()):
            self.blocker.start()
            kb_keys = [n for n, e in self.keys.items() if e["block"] and n not in self.consumer_block]
            log(f"已安裝鍵盤 hook。Consumer 鍵先擋後補: {sorted(self.consumer_block) or '無'}；"
                f"鍵盤鍵只擋自動重複 (第一下會漏): {kb_keys or '無'}")

    def on_remote_event(self, ev: rawinput.RemoteEvent):
        if ev.kind == "move":
            return
        if not ev.from_remote:                       # 被攔的 VK 從別的裝置來
            t = self.recent_consumer.get((ev.vkey, ev.down))
            if t is not None and time.monotonic() - t < 0.2:
                return                               # 其實是遙控器 Consumer 鍵翻譯出來的鍵盤事件 (hDevice 空)，忽略
            if self.verbose:
                log(f"  實體鍵盤 {ev.detail}，補送")
            self.blocker.replay(ev.vkey, ev.down)
            return
        entry = self.keys.get(ev.name)
        if ev.kind == "key" and ev.vkey:
            if ev.vkey in self.blocker.queued:               # Consumer 鍵: 先擋後補
                if entry and entry["block"]:
                    self.recent_consumer[(ev.vkey, ev.down)] = time.monotonic()
                    self.blocker.consume(ev.vkey, ev.down)
                else:
                    self.blocker.replay(ev.vkey, ev.down)
            elif entry and entry["block"]:                   # 鍵盤鍵: 登記，擋自動重複
                self.blocker.expect(ev.vkey, ev.down)
        if self.verbose or (ev.kind == "key" and ev.down) or ev.kind == "wheel":
            log(f"鍵 {ev.name} {'按下' if ev.down else '放開'}" + ("" if entry else " (未設定，照常傳給系統)"))
        if not entry:
            return
        if ev.kind == "wheel":
            self.fire(ev.name, entry, "single")
        elif ev.down:
            self._gesture_down(ev.name, entry)
        else:
            self._gesture_up(ev.name, entry)

    def _notify_status(self):
        if self.on_status:
            try:
                self.on_status(self.connected, self.battery)
            except Exception:  # noqa: BLE001
                pass

    def _g(self, name: str) -> dict:
        return self.gstate.setdefault(name, {"pending_single": None, "long_timer": None,
                                             "long_fired": False, "ignore_up": False})

    def _gesture_down(self, name: str, entry: dict):
        g = self._g(name)
        if g.get("is_down"):                           # 鍵盤自動重複的 down，忽略
            return
        g["is_down"] = True
        if g["pending_single"] is not None:            # 雙擊窗內的第二次按下
            g["pending_single"].cancel()
            g["pending_single"] = None
            g["ignore_up"] = True
            self.fire(name, entry, "double")
            return
        g["long_fired"] = False
        if entry["long"]:
            g["long_timer"] = self.loop.call_later(self.long_ms / 1000, self._fire_long, name, entry)
        elif not entry["double"]:
            g["ignore_up"] = True                      # 只有單擊: 按下就觸發，反應最快
            self.fire(name, entry, "single")

    def _fire_long(self, name: str, entry: dict):
        g = self._g(name)
        g["long_timer"] = None
        g["long_fired"] = True
        self.fire(name, entry, "long")

    def _gesture_up(self, name: str, entry: dict):
        g = self._g(name)
        g["is_down"] = False
        if g["long_timer"] is not None:
            g["long_timer"].cancel()
            g["long_timer"] = None
        if g["long_fired"]:
            g["long_fired"] = False
            return
        if g["ignore_up"]:
            g["ignore_up"] = False
            return
        if entry["double"]:
            g["pending_single"] = self.loop.call_later(self.double_ms / 1000, self._fire_pending_single, name, entry)
        else:
            self.fire(name, entry, "single")

    def _fire_pending_single(self, name: str, entry: dict):
        self._g(name)["pending_single"] = None
        self.fire(name, entry, "single")

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
                winio.run(action["run"])
            elif "keys" in action:
                log(f"  {name} {label} → 送鍵: {action['keys']}")
                winio.send_keys(action["keys"])
            elif "text" in action:
                log(f"  {name} {label} → 打字: {action['text']!r}")
                winio.type_text(action["text"])
        except Exception as e:  # noqa: BLE001
            log(f"  動作 {name} {label} 失敗: {e}")

    # ---------------- 語音 ----------------
    def on_voice_press(self):
        mode = self.voice.get("mode", "builtin")
        log(f"語音鍵按下 ({mode})")
        if self.vmic and mode == "hotkey":
            self.vmic.set_default()        # 先切預設麥克風，App 收到熱鍵開始錄音時才會錄到虛擬麥克風
            time.sleep(0.05)
        if mode == "hotkey":
            try:
                self.voice_vks = winio.parse_combo(self.voice.get("hotkey", "f13"))
            except ValueError as e:
                log(f"  hotkey 設定錯誤: {e}")
                return
            if self.voice.get("hotkey_style", "hold") == "tap":
                winio.send_keys(self.voice.get("hotkey", "f13"))
            else:
                winio.key_down(self.voice_vks)

    def on_voice_release(self):
        mode = self.voice.get("mode", "builtin")
        if mode == "hotkey" and self.voice_vks:
            if self.voice.get("hotkey_style", "hold") == "tap":
                winio.send_keys(self.voice.get("hotkey", "f13"))
            else:
                winio.key_up(self.voice_vks)
            self.voice_vks = []
        if self.vmic and self.loop:
            # 留一點時間讓最後幾幀播完、App 停止錄音，再切回原本的麥克風
            self.loop.call_later(0.8, self._vmic_finish)

    def _vmic_finish(self):
        if self.vmic:
            self.vmic.stop()
            self.vmic.restore_default()

    def on_pcm(self, chunk: bytes, rate: int):
        if self.vmic and self.voice.get("mode", "builtin") == "hotkey":
            self.vmic.start(rate)
            self.vmic.write(chunk)

    def on_utterance(self, pcm: bytes, rate: int):
        secs = len(pcm) / 2 / rate
        if secs < float(self.voice.get("min_seconds", 0.3)):
            log(f"語音太短 ({secs:.1f}s)，忽略")
            return
        mode = self.voice.get("mode", "builtin")
        need_file = self.voice.get("save_wav", True) or (mode == "builtin" and self.stt.get("command"))
        if not need_file:
            return
        out = self.captures / f"voice-{datetime.now():%Y%m%d-%H%M%S}.wav"
        with wave.open(str(out), "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(2)
            wv.setframerate(rate)
            wv.writeframes(pcm)
        log(f"錄音 {secs:.1f}s → {out.name}")
        if mode == "builtin" and self.stt.get("command") and self.loop:
            self.loop.create_task(self.transcribe(out))

    async def transcribe(self, wav: Path):
        cmd = self.stt["command"].replace("{wav}", str(wav))
        log(f"辨識中: {cmd}")
        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        text = stdout.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            log(f"辨識指令失敗 (code {proc.returncode}): {stderr.decode('utf-8', 'replace').strip()[:300]}")
            return
        log(f"辨識結果 ({time.monotonic() - t0:.1f}s): {text!r}")
        if text and self.stt.get("type_result", True):
            winio.type_text(text)
            if self.stt.get("append"):
                winio.send_keys(self.stt["append"])
        if not self.voice.get("save_wav", True):
            wav.unlink(missing_ok=True)

    # ---------------- 連線 ----------------
    def _on_battery(self, _s, data: bytearray):
        if data and data[0] != self.battery:
            self.battery = data[0]
            log(f"電量 {self.battery}%")
            self._notify_status()

    async def ble_loop(self):
        rc = self.cfg.get("remote", {})
        name = rc.get("name", "PlayRemote")
        retry = float(rc.get("reconnect_seconds", 5))
        while not self._stop:
            addr = rc.get("address") or ""
            if not addr:
                try:
                    addr = await probe.find_paired_address_windows(name)
                except Exception as e:  # noqa: BLE001
                    log(f"列出已配對裝置失敗: {e}")
                if not addr:
                    log(f"已配對的藍牙裝置裡找不到 '{name}'，請先在「設定 → 藍牙與裝置」配對；{retry:.0f} 秒後再找")
                    await asyncio.sleep(retry)
                    continue
            # use_cached_services=False: 前一個程序被強制結束時 WinRT 會留下不完整的服務快取，
            # 之後連線會報 Characteristic not found，關掉快取就正常
            client = BleakClient(BLEDevice(addr, name, None), timeout=20.0,
                                 winrt={"use_cached_services": False})
            try:
                log(f"連線 {addr} ...")
                # 遙控器休眠時 connect() 會一直等到它醒來，而且 WinRT 的操作不一定理會取消，
                # 所以不直接 await 它：輪詢等待，收到停止就放棄這個 task
                ctask = asyncio.ensure_future(client.connect())
                while not ctask.done() and not self._stop:
                    await asyncio.wait({ctask}, timeout=0.5)
                if not ctask.done():
                    ctask.cancel()
                    log("停止驅動 (放棄等待中的連線)")
                    break
                ctask.result()
                self.connected = True
                self._notify_status()
                log("已連線，送 GET_CAPS 1.0 (語音鍵: 按住說話)")
                mic = AtvvMic(client, on_utterance=self.on_utterance, on_press=self.on_voice_press,
                              on_release=self.on_voice_release, log=lambda m: log(f"  mic: {m}"),
                              on_pcm=self.on_pcm)
                await mic.start(negotiate_v10=True)
                try:
                    self._on_battery(None, await client.read_gatt_char(BATTERY_UUID))
                    await client.start_notify(BATTERY_UUID, self._on_battery)
                except Exception as e:  # noqa: BLE001
                    log(f"  讀電量失敗: {e}")
                if not self.listener.devices:
                    self.listener.list_devices()
                    log(f"Raw Input: 現在找到 {len(self.listener.devices)} 個 PlayRemote HID 裝置")
                while client.is_connected and not self._stop:
                    await asyncio.sleep(0.1)
                    await mic.tick()
                log("遙控器斷線" if not self._stop else "停止驅動，斷開連線")
            except asyncio.CancelledError:
                log("停止驅動")
                self._stop = True
            except Exception as e:  # noqa: BLE001
                log(f"連線失敗: {type(e).__name__}: {str(e)[:120]}")
            finally:
                self.connected = False
                self.battery = None
                self._notify_status()
                try:
                    if client.is_connected:
                        await asyncio.wait_for(asyncio.shield(client.disconnect()), 5)
                except Exception:  # noqa: BLE001
                    pass
            if not self._stop:
                await asyncio.sleep(retry)

    async def pump_loop(self):
        while not self._stop:
            rawinput.pump()
            self.blocker.flush()
            await asyncio.sleep(0.005)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.start_input()
        self._tasks = [asyncio.ensure_future(self.pump_loop()), asyncio.ensure_future(self.ble_loop())]
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            self.blocker.stop()
            self.listener.stop()
            if self.vmic:
                self.vmic.stop()
                self.vmic.restore_default()

    def request_stop(self):
        """從別的執行緒呼叫：讓 run() 收尾結束 (GUI 重新載入設定時用)。連線中也會被取消。"""
        self._stop = True
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(lambda: [t.cancel() for t in self._tasks if not t.done()])


def run_blocking(driver: "Driver"):
    """在目前執行緒跑驅動直到它停止。不用 asyncio.run：它收尾時會等所有 task，
    而放棄的 WinRT 連線 task 可能永遠不回來；這裡最多等 3 秒就關掉迴圈。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(driver.run())
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.wait(pending, timeout=3))
            except Exception:  # noqa: BLE001
                pass
        loop.close()


def load_config(path: Path) -> dict:
    if not path.exists():
        # PyInstaller 打包時範本在 _internal (sys._MEIPASS)，原始碼執行時在本檔旁邊
        example = Path(getattr(sys, "_MEIPASS", HERE)) / "config.example.toml"
        shutil.copy(example, path)
        log(f"沒有 {path.name}，已從 config.example.toml 複製一份，請視需要修改")
    with path.open("rb") as f:
        return tomllib.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "config.toml"))
    ap.add_argument("--verbose", "-v", action="store_true", help="印出所有原始事件")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    drv = Driver(cfg, verbose=args.verbose)
    try:
        run_blocking(drv)
    except KeyboardInterrupt:
        drv.request_stop()
    finally:
        drv.blocker.stop()
        if drv.vmic:
            drv.vmic.stop()
            drv.vmic.restore_default()
        log("結束。")


if __name__ == "__main__":
    main()
