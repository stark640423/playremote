"""平台選擇層：gui.py 透過這裡拿到驅動與平台差異，不直接 import Windows / macOS 專屬模組。"""
from __future__ import annotations

import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
APP_NAME = "PlayRemote"

if IS_WIN:
    import playremote as drv
    import rawinput
    BLOCKABLE_KEYS = set(rawinput.KEY_NAME_TO_VK)
    FIRST_PRESS_LEAKS = True        # 鍵盤鍵的第一下會漏進目前視窗（Windows 使用者層限制）
    VMIC_SUPPORTED = True
    MIC_OPTION = ("virtual_mic.enabled", "把遙控器的麥克風當成電腦的預設麥克風（需要 VB-CABLE）")
    HOTKEY_DEFAULT = "f8"
    HOTKEY_PLACEHOLDER = "f8、win+h、ctrl+win…"
    STYLE_OPTIONS = [("hold", "按住（hold）"), ("tap", "各按一下（tap，切換式）")]
    VOICE_HINT = "SogaType：熱鍵 f8；SogaType 設「按住說話」用 hold，設「切換」用 tap。Windows 內建語音輸入：win+h、tap。"
    STT_PLACEHOLDER = "內建模式用，{wav} 代表錄音檔，例如 whisper-cli.exe -m model.bin -l zh -nt -np -f {wav}"
    MIC_NOTE = ""
    TRANSPORT_NOTE = "藍牙"
else:
    import playremote_mac as drv
    import hid_mac
    BLOCKABLE_KEYS = set(hid_mac.KEY_NAMES_BY_KEYBOARD.values()) | set(hid_mac.KEY_NAMES_BY_CONSUMER.values())
    FIRST_PRESS_LEAKS = False
    VMIC_SUPPORTED = False
    MIC_OPTION = ("auto_mic", "按住語音鍵時自動把系統麥克風切成「PlayRemote」，放開後切回原本的")
    HOTKEY_DEFAULT = "fn"
    HOTKEY_PLACEHOLDER = "fn、rcmd、f18、ctrl…"
    STYLE_OPTIONS = [("hold", "按住（hold）"), ("tap", "各按一下（tap，切換式）"), ("double", "連按兩下（macOS 聽寫）")]
    VOICE_HINT = ("SogaType / 豆包：熱鍵 fn、hold。Typeless：fn、tap。macOS 內建聽寫：ctrl、連按兩下。"
                  "麥克風：請在語音輸入 App（或系統設定 → 聲音 → 輸入）選「PlayRemote」。")
    STT_PLACEHOLDER = "留空 = macOS 內建離線辨識；或填外部指令，{wav} 代表錄音檔"
    MIC_NOTE = "遙控器的麥克風會以「PlayRemote」出現在系統的輸入裝置裡（USB 接收器）；關掉上面的選項就要自己在語音輸入 App 裡選它。"
    TRANSPORT_NOTE = "USB 接收器"

Driver = drv.Driver
run_blocking = drv.run_blocking
LOG_HANDLERS = drv.LOG_HANDLERS
HERE: Path = drv.HERE
DATA_DIR: Path = getattr(drv, "DATA_DIR", drv.HERE)   # 設定檔 / 記錄檔放哪 (Windows: 程式旁；macOS .app: Application Support)


# ---------------- 開機自啟 ----------------
def _autostart_cmd(script: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--tray"]
    if IS_WIN:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        return [str(pyw if pyw.exists() else sys.executable), str(script), "--tray"]
    return [sys.executable, str(script), "--tray"]


if IS_WIN:
    import winreg

    def autostart_enabled() -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, APP_NAME)
                return True
        except OSError:
            return False

    def set_autostart(on: bool, script: Path):
        cmd = " ".join(f'"{c}"' if " " in c or c.endswith(".py") else c for c in _autostart_cmd(script))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
else:
    import plistlib
    import subprocess

    LABEL = "com.tvsoga.playremote"
    PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    def autostart_enabled() -> bool:
        return PLIST.exists()

    def set_autostart(on: bool, script: Path):
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        if on:
            data = {"Label": LABEL, "ProgramArguments": _autostart_cmd(script), "RunAtLoad": True,
                    "WorkingDirectory": str(script.parent), "StandardOutPath": str(script.parent / "playremote.log"),
                    "StandardErrorPath": str(script.parent / "playremote.log")}
            with PLIST.open("wb") as f:
                plistlib.dump(data, f)
            subprocess.run(["launchctl", "load", "-w", str(PLIST)], capture_output=True)
        else:
            subprocess.run(["launchctl", "unload", "-w", str(PLIST)], capture_output=True)
            PLIST.unlink(missing_ok=True)
