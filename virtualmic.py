"""把遙控器的聲音變成 Windows 的麥克風 (Windows 專用)。

原理：需要一條「虛擬音訊線」(例如 VB-CABLE)：驅動把解碼後的 PCM 播到它的播放端 (CABLE Input)，
Windows 就會在它的錄音端 (CABLE Output) 聽到同樣的聲音。語音鍵按下時把系統預設麥克風切成 CABLE Output，
SogaType 這類用預設麥克風的 App 就會直接錄到遙控器的聲音；放開後切回原本的麥克風。

播放端用裝置自己的取樣率開 (WASAPI 的 CABLE Input 只收 44.1k/48k)，遙控器的 16 kHz 在這裡線性內插升頻。
音訊先放進緩衝區，由 PortAudio 的 callback 拉走，不會卡住 BLE 的通知執行緒。

依賴: sounddevice + numpy (播放)、pycaw + comtypes (切換預設裝置)。
"""
import threading
from typing import Optional

import numpy as np
import sounddevice as sd

try:
    import comtypes
    from comtypes import CLSCTX_ALL, GUID
    from pycaw.api.policyconfig import IPolicyConfig
    from pycaw.utils import AudioUtilities
    _HAVE_PYCAW = True
except Exception:  # noqa: BLE001
    _HAVE_PYCAW = False

CLSID_POLICY_CONFIG = "{870af99c-171d-4f9e-af0d-e63df40c2bc9}"
ROLES = (0, 1, 2)   # eConsole, eMultimedia, eCommunications


def find_output_devices(name_part: str) -> list[int]:
    """用名稱片段找 PortAudio 的播放裝置，依 WASAPI → MME → 其他 排序。"""
    found = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and name_part.lower() in d["name"].lower():
            api = sd.query_hostapis(d["hostapi"])["name"]
            rank = 0 if "WASAPI" in api else 1 if "MME" in api else 2
            found.append((rank, i))
    return [i for _, i in sorted(found)]


def find_capture_endpoint(name_part: str) -> Optional[str]:
    """用名稱片段找 Windows 錄音端點 (MMDevice id，{0.0.1.…} 開頭)。"""
    if not _HAVE_PYCAW:
        return None
    for d in AudioUtilities.GetAllDevices():
        if d.state.value == 1 and d.id.startswith("{0.0.1.") and name_part.lower() in (d.FriendlyName or "").lower():
            return d.id
    return None


class VirtualMic:
    def __init__(self, playback_name: str = "CABLE Input", capture_name: str = "CABLE Output", log=print):
        self.playback_name = playback_name
        self.capture_name = capture_name
        self.log = log
        self.out_candidates = find_output_devices(playback_name)
        self.capture_id = find_capture_endpoint(capture_name)
        self.stream: Optional[sd.OutputStream] = None
        self.in_rate = 0
        self.out_rate = 0
        self.saved_default: Optional[str] = None
        self._policy = None
        self._lock = threading.Lock()
        self._buf = np.zeros(0, dtype=np.float32)
        self._last = 0.0
        self._failed = False
        self.underruns = 0
        self._prebuf = 0          # 開始播放前先累積的取樣數 (約 60 ms)，避免一開始就斷音
        self._primed = False

    @property
    def available(self) -> bool:
        return bool(self.out_candidates) and self.capture_id is not None

    def describe(self) -> str:
        if self.out_candidates:
            d = sd.query_devices(self.out_candidates[0])
            out = f"{d['name']} ({sd.query_hostapis(d['hostapi'])['name']}, {int(d['default_samplerate'])} Hz)"
        else:
            out = "找不到"
        return f"播放端 [{self.playback_name}] → {out}; 錄音端 [{self.capture_name}] → {self.capture_id or '找不到'}"

    # ---- 預設麥克風切換 ----
    def _policy_config(self):
        if self._policy is None:
            self._policy = comtypes.CoCreateInstance(GUID(CLSID_POLICY_CONFIG), IPolicyConfig, CLSCTX_ALL)
        return self._policy

    def set_default(self):
        if not _HAVE_PYCAW or not self.capture_id:
            return
        try:
            cur = AudioUtilities.GetMicrophone().GetId()
            if cur.lower() == self.capture_id.lower():
                return
            self.saved_default = cur
            pc = self._policy_config()
            for role in ROLES:
                pc.SetDefaultEndpoint(self.capture_id, role)
            self.log("預設麥克風 → 虛擬麥克風")
        except Exception as e:  # noqa: BLE001
            self.log(f"切換預設麥克風失敗: {e}")

    def restore_default(self):
        if not self.saved_default:
            return
        try:
            pc = self._policy_config()
            for role in ROLES:
                pc.SetDefaultEndpoint(self.saved_default, role)
            self.log("預設麥克風已切回原本的")
        except Exception as e:  # noqa: BLE001
            self.log(f"還原預設麥克風失敗: {e}")
        finally:
            self.saved_default = None

    # ---- 音訊串流 ----
    def _callback(self, outdata, frames, _time, status):
        with self._lock:
            if not self._primed:
                if len(self._buf) < self._prebuf:
                    outdata[:, 0] = 0.0
                    return
                self._primed = True
            n = min(frames, len(self._buf))
            outdata[:n, 0] = self._buf[:n]
            self._buf = self._buf[n:]
        if n < frames:
            outdata[n:, 0] = 0.0
            if n == 0:
                self.underruns += 1

    def start(self, rate: int):
        """開啟播放端 (以裝置原生取樣率)；已開且輸入取樣率相同就直接回傳。"""
        if self.stream is not None and self.in_rate == rate:
            return
        if self._failed:
            return
        self._close()
        self.in_rate = rate
        self._last = 0.0
        for idx in self.out_candidates:
            d = sd.query_devices(idx)
            for out_rate in (int(d["default_samplerate"]), 48000, 44100, rate):
                try:
                    s = sd.OutputStream(device=idx, samplerate=out_rate, channels=1, dtype="float32",
                                        latency="low", callback=self._callback)
                    s.start()
                    self.stream, self.out_rate = s, out_rate
                    with self._lock:
                        self._buf = np.zeros(0, dtype=np.float32)
                        self._prebuf = int(out_rate * 0.06)
                        self._primed = False
                    self.log(f"虛擬麥克風播放端已開: {d['name']} @ {out_rate} Hz (輸入 {rate} Hz)")
                    return
                except Exception as e:  # noqa: BLE001
                    last_err = e
        self._failed = True
        self.log(f"開啟虛擬麥克風播放端失敗 (這段語音不再重試): {last_err}")

    def write(self, pcm: bytes):
        if self.stream is None:
            return
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if len(x) == 0:
            return
        if self.out_rate != self.in_rate:
            n_out = int(round(len(x) * self.out_rate / self.in_rate))
            xs = np.concatenate(([self._last], x))            # 接上上一塊的最後一個取樣，避免接縫
            pos = (np.arange(n_out) + 1) * (len(x) / n_out)
            x = np.interp(pos, np.arange(len(xs)), xs).astype(np.float32)
        self._last = float(x[-1])
        with self._lock:
            self._buf = np.concatenate((self._buf, x))

    def _close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:  # noqa: BLE001
                pass
            self.stream = None

    def stop(self):
        self._close()
        self._failed = False
        with self._lock:
            self._buf = np.zeros(0, dtype=np.float32)


if __name__ == "__main__":
    import sys
    import time
    names = sys.argv[1:] or ["CABLE Input", "CABLE Output"]
    vm = VirtualMic(*names)
    print(vm.describe())
    if not vm.available:
        print("不可用：請安裝 VB-CABLE 或在設定檔指定正確的裝置名稱")
        sys.exit(1)
    # 自我測試: 播 1 秒 440 Hz 到虛擬線，同時從錄音端錄回來看有沒有聲音
    import sounddevice as _sd
    rate = 16000
    t = np.arange(rate) / rate
    tone = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16).tobytes()
    cap_idx = next((i for i, d in enumerate(_sd.query_devices())
                    if d["max_input_channels"] > 0 and names[1].lower() in d["name"].lower()
                    and "WASAPI" in _sd.query_hostapis(d["hostapi"])["name"]), None)
    rec = []
    cap_rate = int(_sd.query_devices(cap_idx)["default_samplerate"])
    with _sd.InputStream(device=cap_idx, samplerate=cap_rate, channels=1, dtype="float32",
                         callback=lambda ind, n, tt, st: rec.append(ind.copy())):
        vm.start(rate)
        for i in range(0, len(tone), 512):
            vm.write(tone[i:i + 512])
            time.sleep(0.016)
        time.sleep(0.5)
        vm.stop()
    x = np.concatenate(rec)[:, 0]
    rms = float(np.sqrt(np.mean(x ** 2)))
    print(f"錄音端收到 rms={rms:.4f} underruns={vm.underruns} → {'OK，虛擬線可用' if rms > 0.01 else '沒聲音'}")
