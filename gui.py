#!/usr/bin/env python3
"""PlayRemote 設定介面 + 系統匣（Windows / macOS 共用）。

  gui.py [--tray]      --tray: 啟動時只顯示系統匣圖示，不開視窗

視窗裡：遙控器圖、每個鍵的單擊 / 雙擊 / 長按動作、語音鍵設定、連線狀態與電量。
驅動 (playremote.Driver) 在背景執行緒跑；按「儲存並套用」會寫回 config.toml 並重新啟動驅動。
"""
from __future__ import annotations
import argparse
import asyncio
import os
import subprocess
import sys
import warnings
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea,
                               QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget)

import backend
import config_store

HERE = backend.HERE
warnings.filterwarnings("ignore", category=UserWarning, module="pycaw")   # pycaw 列裝置時的 COM 屬性警告，無害
CONFIG_PATH = backend.DATA_DIR / "config.toml"
APP_NAME = "PlayRemote"
GESTURE_LABEL = {"single": "單擊", "double": "雙擊", "long": "長按"}

# 左右兩欄的鍵（鍵名、顯示名稱、圖示字）
LEFT_KEYS = [("power", "電源鍵", "⏻"), ("up", "上鍵", "︿"), ("down", "下鍵", "﹀"), ("left", "左鍵", "〈"),
             ("right", "右鍵", "〉"), ("ok", "確定鍵", "◎"), ("back", "返回鍵", "↶"), ("home", "主頁鍵", "⌂"),
             ("menu", "選單鍵", "≡"), ("backspace", "退格鍵", "⌫")]
RIGHT_KEYS = [(str(d), f"數字 {d}", str(d)) for d in (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)]
UNMAPPABLE = {"blue": "藍色鍵", "green": "綠色鍵", "red": "紅色鍵", "yellow": "黃色鍵", "mouse": "滑鼠鍵"}
KEYBOARD_KEYS = {"up", "down", "left", "right", "backspace", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0"}


def action_summary(act) -> str:
    if not act:
        return "未設定"
    if "run" in act:
        cmd = act["run"].strip().strip('"')
        return "開啟 " + (Path(cmd.split()[0]).stem if cmd else "?")
    if "keys" in act:
        return act["keys"]
    if "text" in act:
        return "打字：" + (act["text"][:8] + ("…" if len(act["text"]) > 8 else ""))
    return "未設定"


def make_icon(size: int = 64) -> QIcon:
    """App 圖示：assets/icon.png（installer/make_icon.py 由遙控器照片產生）；找不到時畫一個簡化的遙控器。"""
    png = Path(getattr(sys, "_MEIPASS", HERE)) / "assets" / "icon.png"
    if png.exists():
        return QIcon(str(png))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#f4f4f6"))
    p.setPen(QPen(QColor("#8a8a8e"), size * 0.05))
    p.drawRoundedRect(QRectF(size * 0.3, size * 0.05, size * 0.4, size * 0.9), size * 0.2, size * 0.2)
    p.setBrush(QColor("#2b2b2e"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(size * 0.36, size * 0.14, size * 0.28, size * 0.28))
    p.setBrush(QColor("#e53935"))
    p.drawEllipse(QRectF(size * 0.44, size * 0.6, size * 0.12, size * 0.12))
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- 驅動執行緒
class DriverWorker(QThread):
    log = Signal(str)
    status = Signal(bool, object)
    key = Signal(str, str)
    crashed = Signal(str)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.driver = None

    def run(self):
        handler = self.log.emit
        backend.LOG_HANDLERS.append(handler)
        try:
            self.driver = backend.Driver(self.cfg, on_status=lambda c, b: self.status.emit(c, b),
                                            on_key=lambda n, g: self.key.emit(n, g))
            backend.run_blocking(self.driver)
        except Exception as e:  # noqa: BLE001
            self.log.emit(f"驅動停止: {type(e).__name__}: {e}")
            self.crashed.emit(f"{type(e).__name__}: {e}")
        finally:
            if handler in backend.LOG_HANDLERS:
                backend.LOG_HANDLERS.remove(handler)
            self.status.emit(False, None)

    def stop(self):
        if self.driver:
            self.driver.request_stop()


class PairWorker(QThread):
    log = Signal(str)
    done = Signal(bool)

    def __init__(self):
        super().__init__()
        self._stop = False

    def run(self):
        try:
            import ble_pair_mac
            ok = ble_pair_mac.pair(on_log=self.log.emit, should_stop=lambda: self._stop)
            self.done.emit(ok)
        except Exception as e:  # noqa: BLE001
            self.log.emit(f"藍牙配對失敗：{type(e).__name__}: {e}")
            self.done.emit(False)

    def stop(self):
        self._stop = True


# ---------------------------------------------------------------- 遙控器圖
class RemoteView(QWidget):
    """遙控器圖（assets/remote.png，沒有就畫示意圖）；按下的鍵會短暫亮起，點圖上的鍵會選到對應卡片。"""

    # 鍵名 → (x, y, 半徑)，相對於圖寬 / 圖高的比例
    HOTSPOTS = {
        "up": (0.502, 0.081, 0.05), "down": (0.502, 0.242, 0.05), "left": (0.345, 0.162, 0.05),
        "right": (0.660, 0.162, 0.05), "ok": (0.502, 0.162, 0.065),
        "back": (0.350, 0.336, 0.055), "home": (0.502, 0.336, 0.055), "power": (0.655, 0.336, 0.055),
        "1": (0.350, 0.414, 0.05), "2": (0.502, 0.414, 0.05), "3": (0.655, 0.414, 0.05),
        "4": (0.350, 0.475, 0.05), "5": (0.502, 0.475, 0.05), "6": (0.655, 0.475, 0.05),
        "7": (0.350, 0.535, 0.05), "8": (0.502, 0.535, 0.05), "9": (0.655, 0.535, 0.05),
        "menu": (0.350, 0.598, 0.05), "0": (0.502, 0.598, 0.05), "backspace": (0.655, 0.598, 0.05),
        "mouse": (0.350, 0.682, 0.055), "voice": (0.655, 0.682, 0.055),
        "blue": (0.502, 0.780, 0.06), "green": (0.374, 0.838, 0.06), "red": (0.630, 0.838, 0.06),
        "yellow": (0.502, 0.904, 0.06),
    }
    clicked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(200, 520)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.active: set[str] = set()
        self._timers: dict[str, QTimer] = {}
        img = Path(getattr(sys, "_MEIPASS", HERE)) / "assets" / "remote.png"
        self.pixmap = QPixmap(str(img)) if img.exists() else None
        self.setMouseTracking(True)
        self._hover = None

    def flash(self, name: str):
        self.active.add(name)
        self.update()
        t = self._timers.get(name)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(lambda n=name: (self.active.discard(n), self.update()))
            self._timers[name] = t
        t.start(350)

    def _geometry(self):
        """回傳 (繪圖矩形)：圖等比例縮到控件內。"""
        if self.pixmap is None:
            return QRectF(0, 0, self.width(), self.height())
        pw, ph = self.pixmap.width(), self.pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        w, h = pw * scale, ph * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _spot_rect(self, name: str) -> QRectF:
        g = self._geometry()
        x, y, r = self.HOTSPOTS[name]
        rr = r * g.width()
        return QRectF(g.x() + x * g.width() - rr, g.y() + y * g.height() - rr, 2 * rr, 2 * rr)

    def _hit(self, pos) -> str | None:
        for name in self.HOTSPOTS:
            if self._spot_rect(name).contains(pos):
                return name
        return None

    def mouseMoveEvent(self, ev):
        h = self._hit(ev.position())
        if h != self._hover:
            self._hover = h
            self.setCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor)
            self.update()

    def mousePressEvent(self, ev):
        h = self._hit(ev.position())
        if h:
            self.flash(h)
            self.clicked.emit(h)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        g = self._geometry()
        if self.pixmap is not None:
            p.drawPixmap(g.toRect(), self.pixmap)
        else:
            p.setPen(QPen(QColor("#b8b8bd"), 1.5))
            p.setBrush(QColor("#fafafa"))
            p.drawRoundedRect(g.adjusted(10, 5, -10, -5), 60, 60)
        for name in self.HOTSPOTS:
            if name in self.active or name == self._hover:
                p.setPen(QPen(QColor(79, 140, 255), 3))
                p.setBrush(QColor(79, 140, 255, 90 if name in self.active else 40))
                p.drawEllipse(self._spot_rect(name))
        p.end()


# ---------------------------------------------------------------- 動作編輯
class ActionDialog(QDialog):
    def __init__(self, parent, title: str, act):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        self.group = QButtonGroup(self)
        self.r_none = QRadioButton("未設定（不做任何事）")
        self.r_run = QRadioButton("開啟程式或執行指令")
        self.r_keys = QRadioButton("送出按鍵組合（例如 ctrl+shift+v、enter、win+d）")
        self.r_text = QRadioButton("打字")
        for i, r in enumerate((self.r_none, self.r_run, self.r_keys, self.r_text)):
            self.group.addButton(r, i)
        self.e_run = QLineEdit()
        self.e_run.setPlaceholderText('例如 notepad、"C:\\Program Files\\...\\app.exe"、code .')
        b_browse = QPushButton("瀏覽…")
        b_browse.clicked.connect(self._browse)
        self.e_keys = QLineEdit()
        self.e_keys.setPlaceholderText("ctrl+shift+v")
        self.e_text = QLineEdit()
        self.e_text.setPlaceholderText("要打進目前視窗的文字")
        lay.addWidget(self.r_none)
        lay.addWidget(self.r_run)
        row = QHBoxLayout()
        row.addWidget(self.e_run)
        row.addWidget(b_browse)
        lay.addLayout(row)
        lay.addWidget(self.r_keys)
        lay.addWidget(self.e_keys)
        lay.addWidget(self.r_text)
        lay.addWidget(self.e_text)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if not act:
            self.r_none.setChecked(True)
        elif "run" in act:
            self.r_run.setChecked(True)
            self.e_run.setText(act["run"])
        elif "keys" in act:
            self.r_keys.setChecked(True)
            self.e_keys.setText(act["keys"])
        elif "text" in act:
            self.r_text.setChecked(True)
            self.e_text.setText(act["text"])

    def _browse(self):
        fn, _ = QFileDialog.getOpenFileName(self, "選擇程式", "", "程式 (*.exe *.bat *.cmd *.lnk);;所有檔案 (*)")
        if fn:
            self.e_run.setText(f'"{fn}"' if " " in fn else fn)
            self.r_run.setChecked(True)

    def result_action(self):
        i = self.group.checkedId()
        if i == 1 and self.e_run.text().strip():
            return {"run": self.e_run.text().strip()}
        if i == 2 and self.e_keys.text().strip():
            return {"keys": self.e_keys.text().strip()}
        if i == 3 and self.e_text.text():
            return {"text": self.e_text.text()}
        return None


# ---------------------------------------------------------------- 按鍵卡片
class KeyCard(QFrame):
    changed = Signal()

    def __init__(self, name: str, title: str, icon: str, entry: dict):
        super().__init__()
        self.name = name
        self.entry = entry
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("KeyCard { background: #f5f5f7; border: 1px solid #e1e1e6; border-radius: 10px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.title = QLabel(f"{icon}  {title}")
        self.title.setStyleSheet("font-weight: 600; font-size: 13px;")
        head.addWidget(self.title)
        head.addStretch()
        self.block = QCheckBox("擋掉原本按鍵")
        self.block.setChecked(entry["block"])
        if name in backend.BLOCKABLE_KEYS:
            if name in KEYBOARD_KEYS and backend.FIRST_PRESS_LEAKS:
                self.block.setToolTip("此鍵的第一下仍會傳給目前視窗（Windows 限制），長按的重複會擋掉")
            self.block.toggled.connect(self._on_block)
            head.addWidget(self.block)
        lay.addLayout(head)
        row = QHBoxLayout()
        self.buttons = {}
        for g in ("single", "double", "long"):
            b = QPushButton()
            b.setMinimumHeight(44)
            b.setStyleSheet("QPushButton { background: #e8e8ec; border: none; border-radius: 8px; padding: 4px; }"
                            "QPushButton:hover { background: #d8d8e0; }")
            b.clicked.connect(lambda _c=False, gg=g: self._edit(gg))
            self.buttons[g] = b
            row.addWidget(b)
        lay.addLayout(row)
        self._refresh()

    def _refresh(self):
        for g, b in self.buttons.items():
            b.setText(f"{GESTURE_LABEL[g]}\n{action_summary(self.entry.get(g))}")

    def _edit(self, g: str):
        dlg = ActionDialog(self, f"{self.title.text().strip()} — {GESTURE_LABEL[g]}", self.entry.get(g))
        if dlg.exec() == QDialog.Accepted:
            self.entry[g] = dlg.result_action()
            self._refresh()
            self.changed.emit()

    def _on_block(self, on: bool):
        self.entry["block"] = on
        self.changed.emit()

    def highlight(self, on: bool):
        self.setStyleSheet("KeyCard { background: %s; border: 1px solid %s; border-radius: 10px; }"
                           % (("#e3edff", "#4f8cff") if on else ("#f5f5f7", "#e1e1e6")))


# ---------------------------------------------------------------- 語音卡片
class VoiceCard(QFrame):
    changed = Signal()

    def __init__(self, voice: dict):
        super().__init__()
        self.voice = voice
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("VoiceCard { background: #f5f5f7; border: 1px solid #e1e1e6; border-radius: 10px; }")
        lay = QGridLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        t = QLabel("🎤  語音鍵")
        t.setStyleSheet("font-weight: 600; font-size: 13px;")
        lay.addWidget(t, 0, 0, 1, 2)
        lay.addWidget(QLabel("按住說話時："), 1, 0)
        self.mode = QComboBox()
        self.mode.addItems(["轉給其他語音輸入 App（按住它的熱鍵）", "內建：放開後跑辨識指令並打字", "只存錄音檔"])
        self.mode.setCurrentIndex({"hotkey": 0, "builtin": 1, "none": 2}.get(voice.get("mode"), 0))
        lay.addWidget(self.mode, 1, 1)
        lay.addWidget(QLabel("熱鍵："), 2, 0)
        hk = QHBoxLayout()
        self.hotkey = QLineEdit(voice.get("hotkey", backend.HOTKEY_DEFAULT))
        self.hotkey.setPlaceholderText(backend.HOTKEY_PLACEHOLDER)
        self.style = QComboBox()
        self.style.addItems([label for _k, label in backend.STYLE_OPTIONS])
        style_keys = [k for k, _l in backend.STYLE_OPTIONS]
        self.style.setCurrentIndex(style_keys.index(voice.get("hotkey_style")) if voice.get("hotkey_style") in style_keys else 0)
        hk.addWidget(self.hotkey)
        hk.addWidget(self.style)
        lay.addLayout(hk, 2, 1)
        mic_key, mic_label = backend.MIC_OPTION
        self.vmic = QCheckBox(mic_label)
        self.vmic.setChecked(bool(voice.get("virtual_mic", {}).get("enabled")) if mic_key == "virtual_mic.enabled"
                             else bool(voice.get(mic_key, True)))
        self.vmic_note = QLabel(backend.MIC_NOTE)
        self.vmic_note.setStyleSheet("color: #888; font-size: 11px;")
        self.vmic_note.setWordWrap(True)
        lay.addWidget(self.vmic, 3, 0, 1, 2)
        lay.addWidget(self.vmic_note, 4, 0, 1, 2)
        lay.addWidget(QLabel("辨識指令："), 5, 0)
        self.stt = QLineEdit(voice.get("stt", {}).get("command", ""))
        self.stt.setPlaceholderText(backend.STT_PLACEHOLDER)
        lay.addWidget(self.stt, 5, 1)
        self.save_wav = QCheckBox("保留每段錄音（captures/）")
        self.save_wav.setChecked(bool(voice.get("save_wav")))
        lay.addWidget(self.save_wav, 6, 0, 1, 2)
        hint = QLabel(backend.VOICE_HINT)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        hint.setWordWrap(True)
        lay.addWidget(hint, 7, 0, 1, 2)
        for w in (self.mode, self.style):
            w.currentIndexChanged.connect(lambda _i: self.changed.emit())
        for w in (self.hotkey, self.stt):
            w.textChanged.connect(lambda _t: self.changed.emit())
        for w in (self.vmic, self.save_wav):
            w.toggled.connect(lambda _b: self.changed.emit())
        if backend.VMIC_SUPPORTED:
            QTimer.singleShot(200, self._check_vmic)

    def _check_vmic(self):
        try:
            import virtualmic
            vm = virtualmic.VirtualMic(self.voice.get("virtual_mic", {}).get("playback", "CABLE Input"),
                                       self.voice.get("virtual_mic", {}).get("capture", "CABLE Output"), log=lambda m: None)
            self.vmic_note.setText("已找到 VB-CABLE，可以使用" if vm.available else
                                   "找不到 VB-CABLE：請到 vb-audio.com/Cable 安裝後重開電腦")
        except Exception as e:  # noqa: BLE001
            self.vmic_note.setText(f"虛擬麥克風不可用：{e}")

    def apply_to(self, voice: dict):
        voice["mode"] = ["hotkey", "builtin", "none"][self.mode.currentIndex()]
        voice["hotkey"] = self.hotkey.text().strip() or backend.HOTKEY_DEFAULT
        voice["hotkey_style"] = backend.STYLE_OPTIONS[self.style.currentIndex()][0]
        mic_key = backend.MIC_OPTION[0]
        if mic_key == "virtual_mic.enabled":
            voice.setdefault("virtual_mic", {})["enabled"] = self.vmic.isChecked()
        else:
            voice[mic_key] = self.vmic.isChecked()
        voice["stt"]["command"] = self.stt.text().strip()
        voice["save_wav"] = self.save_wav.isChecked()


# ---------------------------------------------------------------- 主視窗
class MainWindow(QMainWindow):
    def __init__(self, start_hidden: bool = False):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} 設定")
        self.setWindowIcon(make_icon())
        self.resize(1080, 760)
        self.cfg = config_store.load(CONFIG_PATH, HERE)
        self.dirty = False
        self.worker: DriverWorker | None = None
        self.cards: dict[str, KeyCard] = {}
        self._build()
        self._build_tray()
        self.start_driver()
        if not start_hidden:
            self.show()

    # ---- UI ----
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        # 標題列
        top = QHBoxLayout()
        title = QLabel("按鍵映射")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        top.addWidget(title)
        self.enabled = QCheckBox("啟用自定義按鍵功能")
        self.enabled.setChecked(bool(self.cfg["input"].get("enabled", True)))
        self.enabled.toggled.connect(lambda _b: self._mark_dirty())
        top.addWidget(self.enabled)
        top.addStretch()
        self.device = QLabel()
        self.device.setStyleSheet("QLabel { background: #eef3ff; border: 1px solid #c7d6ff; border-radius: 10px; padding: 8px 14px; }")
        top.addWidget(self.device)
        outer.addLayout(top)
        self._set_status(False, None)
        # 三欄
        cols = QHBoxLayout()
        left = QVBoxLayout()
        for name, t, ic in LEFT_KEYS:
            left.addWidget(self._card(name, t, ic))
        left.addStretch()
        cols.addWidget(self._scroll(left), 4)
        self.remote = RemoteView()
        self.remote.clicked.connect(self._on_remote_click)
        cols.addWidget(self.remote, 3)
        right = QVBoxLayout()
        self.voice_card = VoiceCard(self.cfg["voice"])
        self.voice_card.changed.connect(self._mark_dirty)
        right.addWidget(self.voice_card)
        for name, t, ic in RIGHT_KEYS:
            right.addWidget(self._card(name, t, ic))
        right.addStretch()
        cols.addWidget(self._scroll(right), 4)
        outer.addLayout(cols, 1)
        # 底部
        bottom = QHBoxLayout()
        self.statusline = QLabel("")
        self.statusline.setStyleSheet("color: #555;")
        bottom.addWidget(self.statusline, 1)
        b_log = QPushButton("記錄")
        b_log.setCheckable(True)
        b_log.toggled.connect(lambda on: self.logview.setVisible(on))
        bottom.addWidget(b_log)
        if backend.BLE_PAIR:
            bottom.addWidget(QLabel("連接："))
            self.transport = QComboBox()
            self.transport.addItems(["USB 接收器", "藍牙"])
            self.transport.setCurrentIndex(1 if self.cfg["remote"].get("transport") == "ble" else 0)
            self.transport.setToolTip("USB：插上接收器即用。藍牙：先用修補韌體在系統設定配對，再選這個。")
            self.transport.currentIndexChanged.connect(self._mark_dirty)
            bottom.addWidget(self.transport)
            self.b_pair = QPushButton("藍牙配對")
            self.b_pair.setToolTip("遙控器切到藍牙模式、長按 Home + 返回進配對模式後，按這裡觸發配對")
            self.b_pair.clicked.connect(self.start_pairing)
            bottom.addWidget(self.b_pair)
        b_open = QPushButton("開啟設定檔")
        b_open.clicked.connect(lambda: backend.open_path(CONFIG_PATH))
        bottom.addWidget(b_open)
        self.b_save = QPushButton("儲存並套用")
        self.b_save.setStyleSheet("QPushButton { background: #2f6fed; color: white; padding: 6px 18px; border-radius: 6px; font-weight: 600; }")
        self.b_save.clicked.connect(self.save_and_apply)
        bottom.addWidget(self.b_save)
        outer.addLayout(bottom)
        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setMaximumBlockCount(500)
        self.logview.setFixedHeight(160)
        self.logview.setVisible(False)
        outer.addWidget(self.logview)

    def _scroll(self, layout: QVBoxLayout) -> QScrollArea:
        w = QWidget()
        w.setLayout(layout)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(w)
        return sa

    def _card(self, name: str, title: str, icon: str) -> KeyCard:
        entry = self.cfg["keys"].setdefault(name, {"block": False, "single": None, "double": None, "long": None})
        card = KeyCard(name, title, icon, entry)
        card.changed.connect(self._mark_dirty)
        self.cards[name] = card
        return card

    def _mark_dirty(self):
        self.dirty = True
        self.b_save.setText("儲存並套用 ●")

    def _set_status(self, connected: bool, battery):
        dot = "🟢" if connected else "⚪"
        text = f"<b>{self.cfg['remote'].get('name', 'PlayRemote')}</b><br>{dot} {'已連線' if connected else '未連線'}"
        if battery is not None:
            text += f"　🔋 {battery}%"
        self.device.setText(text)
        if self.tray if hasattr(self, "tray") else False:
            self.tray.setToolTip(f"{APP_NAME}：{'已連線' if connected else '未連線'}" + (f"，電量 {battery}%" if battery is not None else ""))

    # ---- 驅動 ----
    def start_driver(self):
        self.stop_driver()
        self.worker = DriverWorker(self.cfg)
        self.worker.log.connect(self._on_log)
        self.worker.status.connect(self._set_status)
        self.worker.key.connect(self._on_key)
        self.worker.crashed.connect(self._on_crash)
        self.worker.start()
        self.statusline.setText("驅動啟動中…")

    def stop_driver(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(30000)
        self.worker = None

    def _on_crash(self, msg: str):
        self.device.setText("<b>驅動已停止</b><br>⚠ 發生錯誤，請看「記錄」")
        self.logview.setVisible(True)
        QMessageBox.warning(self, APP_NAME, f"驅動發生錯誤而停止：" + chr(10) + msg + chr(10) * 2 + "修正設定後按「儲存並套用」或從系統匣「重新啟動驅動」。")

    def _on_log(self, line: str):
        self.logview.appendPlainText(line)
        if "⚠" in line:
            msg = line.split("⚠", 1)[1].strip()
            shown = getattr(self, "_warned", set())
            if msg not in shown:
                shown.add(msg)
                self._warned = shown
                QTimer.singleShot(0, lambda m=msg: QMessageBox.warning(self, APP_NAME, m))
        self.statusline.setText(line[10:])
        try:
            with (backend.DATA_DIR / "playremote.log").open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _on_remote_click(self, name: str):
        card = self.cards.get(name)
        if card:
            card.highlight(True)
            QTimer.singleShot(600, lambda: card.highlight(False))
            sa = card.parent()
            while sa is not None and not isinstance(sa, QScrollArea):
                sa = sa.parent()
            if sa is not None:
                sa.ensureWidgetVisible(card)
        elif name == "voice":
            self.voice_card.setFocus()
        elif name in UNMAPPABLE:
            self.statusline.setText(f"{UNMAPPABLE[name]}：按下時不會送任何訊號給電腦（可能是紅外線或遙控器本機功能），無法設定")

    def _on_key(self, name: str, gesture: str):
        self.remote.flash(name)
        card = self.cards.get(name)
        if card:
            card.highlight(True)
            QTimer.singleShot(400, lambda: card.highlight(False))

    def start_pairing(self):
        if getattr(self, "pair_worker", None) and self.pair_worker.isRunning():
            return
        self.b_pair.setEnabled(False)
        self.b_pair.setText("配對中…")
        self.logview.setVisible(True)
        self.pair_worker = PairWorker()
        self.pair_worker.log.connect(self._on_log)
        self.pair_worker.done.connect(self._pairing_done)
        self.pair_worker.start()

    def _pairing_done(self, ok: bool):
        self.b_pair.setEnabled(True)
        self.b_pair.setText("藍牙配對")
        if ok:
            QMessageBox.information(self, APP_NAME, "已完成藍牙連線。請到「系統設定 → 藍牙 → 我的裝置」確認 PlayRemote 已連線，再按遙控器測試。")
        else:
            QMessageBox.warning(self, APP_NAME, "沒有連上 PlayRemote。請確認遙控器切到藍牙模式、長按 Home + 返回進入配對模式（燈快閃），再試一次。")

    def save_and_apply(self):
        self.cfg["input"]["enabled"] = self.enabled.isChecked()
        if hasattr(self, "transport"):
            self.cfg["remote"]["transport"] = "ble" if self.transport.currentIndex() == 1 else "usb"
        self.voice_card.apply_to(self.cfg["voice"])
        try:
            config_store.save(CONFIG_PATH, self.cfg)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, APP_NAME, f"寫入設定失敗：{e}")
            return
        self.dirty = False
        self.b_save.setText("儲存並套用")
        self.statusline.setText("已儲存，重新啟動驅動…")
        self.cfg = config_store.load(CONFIG_PATH, HERE)
        self.start_driver()

    # ---- 系統匣 ----
    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        a_open = QAction("開啟設定", self)
        a_open.triggered.connect(self.show_window)
        a_restart = QAction("重新啟動驅動", self)
        a_restart.triggered.connect(self.start_driver)
        self.a_autostart = QAction("開機時自動啟動", self)
        self.a_autostart.setCheckable(True)
        self.a_autostart.setChecked(self.autostart_enabled())
        self.a_autostart.toggled.connect(self.set_autostart)
        a_quit = QAction("結束", self)
        a_quit.triggered.connect(self.quit)
        for a in (a_open, a_restart, self.a_autostart):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_window() if r == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()

    def show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def closeEvent(self, ev):
        if self.tray.isVisible():
            ev.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "仍在背景執行，從系統匣圖示可以再打開或結束。", make_icon(), 3000)
        else:
            self.quit()

    def quit(self):
        self.tray.hide()
        self.stop_driver()
        QApplication.quit()

    # ---- 開機自啟 ----
    @staticmethod
    def autostart_enabled() -> bool:
        return backend.autostart_enabled()

    def set_autostart(self, on: bool):
        try:
            backend.set_autostart(on, Path(__file__).resolve())
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"設定開機自啟失敗：{e}")

def main():
    # 子程序模式：被驅動呼叫來跑離線辨識（自己的 main thread + run loop，Speech 回呼才收得到）。
    # 必須在建 QApplication 之前處理，純 CLI、不開視窗。
    if len(sys.argv) >= 3 and sys.argv[1] == "--stt":
        import stt
        try:
            print(stt.transcribe_file(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "zh-TW"))
            sys.exit(0)
        except Exception as e:  # noqa: BLE001
            print(f"stt error: {e}", file=sys.stderr)
            sys.exit(1)
    ap = argparse.ArgumentParser()
    ap.add_argument("--tray", action="store_true", help="啟動時只顯示系統匣圖示")
    args = ap.parse_args()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    f = app.font()
    f.setPointSize(10)
    app.setFont(f)
    win = MainWindow(start_hidden=args.tray)
    rc = app.exec()
    win.stop_driver()
    sys.exit(rc)


if __name__ == "__main__":
    main()
