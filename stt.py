"""macOS 內建語音辨識 (Speech framework)，離線、免安裝。

用法:
  stt.py <wav 檔> [--lang zh-TW]
"""
import argparse
import sys
import threading

import Foundation
import Speech


def _request_auth(timeout=30.0) -> bool:
    status = Speech.SFSpeechRecognizer.authorizationStatus()
    if status == Speech.SFSpeechRecognizerAuthorizationStatusAuthorized:
        return True
    done = threading.Event()
    result = {}

    def cb(s):
        result["s"] = s
        done.set()

    Speech.SFSpeechRecognizer.requestAuthorization_(cb)
    # 等待授權對話框期間要讓 run loop 跑，否則 callback 不會回來
    rl = Foundation.NSRunLoop.currentRunLoop()
    deadline = Foundation.NSDate.dateWithTimeIntervalSinceNow_(timeout)
    while not done.is_set() and deadline.timeIntervalSinceNow() > 0:
        rl.runMode_beforeDate_(Foundation.NSDefaultRunLoopMode,
                               Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1))
    return result.get("s") == Speech.SFSpeechRecognizerAuthorizationStatusAuthorized


def transcribe_file(path: str, lang: str = "zh-TW", on_device: bool = True, timeout: float = 60.0) -> str:
    """把 WAV/音訊檔轉成文字。回傳最終辨識結果，失敗則拋出例外。"""
    if not _request_auth():
        raise RuntimeError("沒有語音辨識權限。請到「系統設定 → 隱私權與安全性 → 語音辨識」允許。")
    locale = Foundation.NSLocale.localeWithLocaleIdentifier_(lang)
    rec = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale)
    if rec is None or not rec.isAvailable():
        raise RuntimeError(f"語言 {lang} 的辨識器不可用。")
    url = Foundation.NSURL.fileURLWithPath_(path)
    req = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(url)
    req.setShouldReportPartialResults_(False)
    if on_device and rec.supportsOnDeviceRecognition():
        req.setRequiresOnDeviceRecognition_(True)

    done = threading.Event()
    out = {}

    def handler(result, error):
        if error is not None:
            out["error"] = str(error)
            done.set()
            return
        if result is not None:
            out["text"] = str(result.bestTranscription().formattedString())
            if result.isFinal():
                done.set()

    rec.recognitionTask_resultHandler_(req, handler)
    rl = Foundation.NSRunLoop.currentRunLoop()
    deadline = Foundation.NSDate.dateWithTimeIntervalSinceNow_(timeout)
    while not done.is_set() and deadline.timeIntervalSinceNow() > 0:
        rl.runMode_beforeDate_(Foundation.NSDefaultRunLoopMode,
                               Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1))
    if "error" in out and "text" not in out:
        raise RuntimeError(out["error"])
    if not done.is_set():
        raise TimeoutError("辨識逾時")
    return out.get("text", "")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("wav")
    p.add_argument("--lang", default="zh-TW")
    p.add_argument("--cloud", action="store_true", help="允許使用 Apple 雲端辨識")
    a = p.parse_args()
    try:
        print(transcribe_file(a.wav, a.lang, on_device=not a.cloud))
    except Exception as e:  # noqa: BLE001
        print("失敗:", e, file=sys.stderr)
        sys.exit(1)
