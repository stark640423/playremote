# macOS 版交接（給 Mac 那邊的工作階段）

更新：2026-10-14。Windows 版已可用並發行（v0.1.0），這份文件把 Windows 這邊學到的東西整理成 Mac 版要做的事。

**先做這件事**：Mac 上舊的專案資料夾不要再用（歷史不同）。重新 clone：

```bash
git clone https://github.com/stark640423/playremote.git
cd playremote
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

開工前讀 `STATUS.md`（進度）和 `NOTES.md`（協定、按鍵對照、ATVV 0.4/1.0 行為）。這兩份是兩邊共用的事實來源。

## 2026-10-14 更新：gui.py 已改為跨平台

`gui.py` 不再直接 import `playremote` / `rawinput` / `winreg`，改經 `backend.py`（依平台選驅動、可擋的鍵、語音熱鍵選項、開機自啟）。
Windows 這邊若再改 `gui.py`，平台專屬的東西請放進 `backend.py` 的 Windows 分支，不要直接 import Windows 模組。
`config_store.example_path()` 在 macOS 會改用 `config.example.mac.toml`。

## 2026-10-15 App 圖示

`installer/make_icon.py` 由 `assets/remote.png` 產生 `assets/icon.png`、`PlayRemote.icns`、`PlayRemote.ico`。
Windows：release.yml 的 PyInstaller 加了 `--icon assets/PlayRemote.ico`，`playremote.iss` 加了 `SetupIconFile` / `UninstallDisplayIcon`。
`gui.make_icon()` 改用 `assets/icon.png`（視窗、系統匣、選單列）。

## 分工

- **Windows 這邊正在做**：設定介面（PySide6，SayAll 風格的按鍵映射頁）和系統匣。會動到 `playremote.py`、新增 `gui.py`，
  並可能微調 `config.example.toml` 的欄位。**Mac 這邊不要改這幾個檔案**；要改共用檔（`audio.py`、`probe.py`、`config.example.toml`）先在 commit 訊息說明。
- **Mac 這邊負責**：1) 解決配對；2) 做 `playremote_mac.py`（與 Windows 版同一份 `config.toml`）。

## 第一關：配對

### 已知事實

- 遙控器**有** HID over GATT（服務 1812），Windows 一次就配對成功，所以不是遙控器沒有 HID。
- macOS 在「系統設定 → 藍牙」按「連接」永遠轉圈；`hidutil list` 沒有裝置；CoreBluetooth 對 App 隱藏 1812。
- 遙控器被任何一台主機連上後就不再廣播。**它現在和 Windows PC 配對中**，只要那台 PC 開著藍牙，遙控器一醒來就會連回去，
  Mac 根本掃不到。這類遙控器通常只保存一組 bonding。
- 系統「忘記裝置」不會清掉遙控器端的金鑰；金鑰不符時遙控器對連線請求沒反應（就是轉圈的樣子）。

### 診斷步驟（照順序）

1. **把 Windows PC 的藍牙關掉**（或關機），確定遙控器不會被它接走。
2. **重置遙控器的配對**：拔電池 10 秒再裝回，進入配對模式（通常是長按 Home + 返回，或依說明書）。LED 應該閃。
3. Mac 上先用 `./.venv/bin/python probe.py scan`，確認掃得到 `PlayRemote`（掃不到就是還被別台接走或沒進配對模式）。
4. 系統設定 → 藍牙 → 連接。若仍轉圈，開 **PacketLogger**（Xcode → Open Developer Tool → More Developer Tools 下載
   Additional Tools，裡面有 PacketLogger），重做一次配對，看 SMP（Security Manager）那段：
   - `Pairing Failed` 的 reason 是什麼（Authentication Requirements / Pairing Not Supported / Confirm Value Failed …）。
   - 遙控器要求的 IO capability 和是否要 LE Secure Connections。
   這是唯一能看到真正失敗原因的方法，結果請記進 `NOTES.md`「連線行為」。
5. 替代路徑：用 `probe.py dump` 連上後，嘗試讀一個需要加密的特徵值（例如 HID 的 Report Map `2A4B`，如果 CoreBluetooth 願意露出來；
   或 OTA 服務的 `…2b12`）。讀取需要加密時 macOS 會自己跳配對對話框，有時比系統設定的「連接」更容易成功。
6. 若 PacketLogger 顯示遙控器要 passkey 或 OOB：這隻遙控器沒有顯示器，應該是 Just Works；若 macOS 堅持要 MITM 保護而失敗，
   記錄下來，可能需要用 IOBluetooth 私有 API 或改走「不配對、只用 ATVV」的降級方案（按鍵就無法用 HID 收，只剩語音）。

配對成功的判準：`hidutil list` 看得到 VID 0x2D01 / PID 0x5000 的裝置，按方向鍵游標會動。

## 第二關：macOS 驅動架構（配對成功後）

Windows 版的架構在 `playremote.py`，Mac 版照同一個設定檔做，對應關係：

| 功能 | Windows 做法 | macOS 建議做法 |
|---|---|---|
| 找裝置、連線、ATVV 麥克風 | `probe.py` + `audio.py`（bleak） | **直接共用**。`probe.py` 的 macOS 分支用 `retrieveConnectedPeripherals` 找已連線裝置；`AtvvMic` 跨平台 |
| 收按鍵、分辨來源 | Raw Input + 低階 hook，數字鍵第一下會漏 | **IOKit HID**：`IOHIDManager` 以 VID/PID 比對，用 `kIOHIDOptionsTypeSeizeDevice` **獨占**裝置，所有鍵都能完整攔下（比 Windows 強）。需要「輸入監控」權限。pyobjc 沒有包 IOKit HID，用 `ctypes` 載 `IOKit.framework` 或改用 `hidapi`（hidapi 在 macOS 可以開鍵盤 collection） |
| 單擊/雙擊/長按 | `playremote.py` 的 `_gesture_*` | 搬成共用模組（例如 `gestures.py`），Windows 這邊會配合 |
| 送組合鍵、打字、啟動程式 | `winio.py`（SendInput） | `Quartz.CGEvent`（已在 requirements 的 pyobjc-framework-Quartz）；啟動程式用 `open -a`。需要「輔助使用」權限 |
| 語音鍵 → 其他 App 的熱鍵 | 按住 F8 給 SogaType | 同樣用 CGEvent 按住；SogaType macOS 版的觸發鍵是 **Fn**（CGEvent 要設 `kCGEventFlagMaskSecondaryFn`）或左/右 Command |
| 遙控器麥克風 → 電腦麥克風 | VB-CABLE + `IPolicyConfig` 切預設麥克風 | 虛擬音訊裝置用 **BlackHole**（注意它是 GPL-3，只能請使用者自行安裝，不能包）；切預設輸入裝置用 CoreAudio `kAudioHardwarePropertyDefaultInputDevice`（pyobjc CoreAudio 或 `switchaudio-osx`） |
| 內建辨識 | 外部指令 | `stt.py` 已有 Speech framework 離線辨識，可直接接 |
| 常駐 | 命令列 / 系統匣（製作中） | 選單列 App（rumps 或 PySide6 的 QSystemTrayIcon） |

ATVV 的部分 Mac 上已經驗證過 0.4 模式；1.0 模式（送 `0a 01 00 00 03 03`）在 Mac 上還沒試，理論上一樣。

## 兩邊都要遵守的事

- 不要把任何裝置位址、錄音紀錄、API key、內部專案路徑 commit 進 repo（`captures/`、`config.toml` 已被 .gitignore 排除）。
- 新發現寫進 `NOTES.md`，進度寫進 `STATUS.md`，commit 訊息用繁體中文。
- 發行用標籤：推 `v*` 標籤會觸發 Windows 的自動打包；Mac 的打包流程之後另外加 job。

## 還沒驗證、Mac 上可以順便做的實驗

- ATVV 的 MIC_CLOSE / MIC_EXTEND / 主機主動 MIC_OPEN（`atvv_probe.py --close-after 3` / `--extend 5` / `--mic-open`）。
- 1.0 模式最長串流秒數（按住超過 18 秒會不會被切）。
- 彩色四鍵和滑鼠圖示鍵：`probe.py watch` 時按它們，看 `1920/2b00` 或 `0aa0/b002` 有沒有資料。
