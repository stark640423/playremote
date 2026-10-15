# 專案狀態與路線圖

更新：2026-10-14

## 目標

開源的 PlayRemote 驅動程式（Windows 已可用，macOS 規劃中）：

1. 開啟後自動連上遙控器，斷線自動重連。
2. 每個鍵可自訂單擊 / 雙擊 / 長按動作（啟動程式、送組合鍵、打字）。
3. 語音鍵按住說話，觸發方式可選：
   - 內建：驅動自己辨識，文字打進目前視窗（辨識引擎可換）。
   - 轉給其他語音輸入 App：語音鍵變成那個 App 的熱鍵（按住或切換），並可把遙控器的麥克風變成電腦的預設麥克風，
     讓那個 App 直接錄到遙控器的聲音（SogaType、Windows 內建語音輸入等）。

參考過的專案：[SayAll 無線麥](https://github.com/HD838A/remote-mic-app)（Swift、GPL-3.0）。本專案自行實作，不 fork，授權 MIT。

## 目前進度

| 項目 | 狀態 |
|---|---|
| BLE 連線、列出 GATT | 完成（`probe.py`） |
| 麥克風協定、解碼 | 完成。遙控器預設 ATVV 0.4 模式（按一下送 18 秒）；送 1.0 的 GET_CAPS 後變成按住說話、16 kHz、放開即停。`audio.py` 的 `AtvvMic` 供驅動使用 |
| 按鍵、陀螺儀、滾輪 | 完成。標準 HID（服務 1812），對照表在 `NOTES.md` |
| Windows 驅動（`playremote.py`） | **可用**：自動連線重連、電量、每鍵單擊/雙擊/長按、Consumer 鍵擋乾淨、鍵盤鍵只擋自動重複（第一下會漏，見 NOTES）、語音鍵 → 熱鍵 + 虛擬麥克風 |
| 語音鍵驅動其他語音輸入 App | 完成並實測（SogaType 按住說話模式：`hotkey = "f8"`, `hotkey_style = "hold"`） |
| 遙控器麥克風 → 電腦麥克風 | 完成並實測（需 VB-CABLE） |
| 內建離線語音辨識 | 未接。`[voice.stt] command` 可直接填 whisper.cpp 之類的指令，或之後加 Python 內建引擎 |
| 設定介面（`gui.py`） | **完成**（v0.2.0）：遙控器照片熱點、每鍵單擊/雙擊/長按、語音鍵選項、連線與電量、儲存即套用 |
| 安裝程式 | **Windows 與 macOS 皆完成**。macOS：`installer/build_mac.sh`（PyInstaller .app + DMG，ad-hoc 簽名，Info.plist 權限說明），GitHub Actions `macos` job 隨 `v*` 標籤發行。Windows：PyInstaller + Inno Setup（`installer/playremote.iss`），per-user 安裝、可選開機自啟；GitHub Actions 推 `v*` 標籤自動建 Release。未簽章 |
| 系統匣 | **完成**：關閉視窗縮到系統匣，選單可重啟驅動、開機自啟、結束 |
| macOS 版驅動（`playremote_mac.py`，USB 接收器） | **原型完成**：IOHIDManager 攔鍵（block 介面獨占、未設定鍵補送）、單擊/雙擊/長按、run/keys/text 動作（CGEvent）、語音鍵 hotkey（Fn 給 SogaType）/ builtin（USB 麥克風錄音 + macOS 內建辨識）。**v0.3.3 使用者實機驗收通過**（按鍵動作、介面亮燈、儲存並套用、語音鍵 Fn + 自動切換麥克風）。發行：已簽署並公證的 DMG，CI 隨 `v*` 標籤自動產生 |
| macOS 藍牙 | 探測工具齊全。**藍牙：已確認為韌體問題**（Appearance 在掃描回應且為 0x0180，設定頁不分類為 HID；第三方 App 無法發起配對，見 `docs/APPLE_BLE_HID_CHECKLIST.md`）。**USB 接收器：按鍵/陀螺儀/滾輪/麥克風實測可用**，Mac 版以此為主 |
| 彩色四鍵、滑鼠圖示鍵 | 不送任何訊號給電腦（HID、ATVV、自訂服務都沒有），應為紅外線或本機功能，無法對應 |

## 2026-10-14 重大發現：USB 2.4G 接收器模式

遙控器附的 USB 接收器插上 Mac 後，枚舉成一個 USB 複合裝置 `PlayRemote`（VID 0x0610 / PID 0x1000）：

- **USB 音訊**：麥克風，16 kHz 單聲道，系統直接看到「PlayRemote」輸入裝置。
- **HID**：滑鼠（page 1/usage 2，陀螺儀與滾輪）、遊戲控制器（1/5）、Consumer Control（12/1）、vendor 頁（0xFF00/1）。
  沒有標準鍵盤介面，按鍵不會直接打字。

意義：在 macOS 上**完全繞過藍牙配對問題**，也不需要 BlackHole 虛擬麥克風。
Mac 版可以改成「USB 接收器優先」：用 IOHIDManager 攔 HID 報告做熱鍵，語音輸入法直接選 PlayRemote 麥克風。
Windows 版也可評估同樣走 USB（鍵盤攔截問題可能一併消失）。

工具：`hidwatch_mac.py`（監看 USB HID 報告）、`usb_mic_test.py`（測 USB 麥克風是否只在按住語音鍵時送聲音）。

## 已知限制

- **數字鍵、方向鍵、退格的第一下會漏進目前視窗**。這是 Windows 使用者層 API 的極限（鍵盤 hook 當下分不出來源），
  要徹底擋掉需要核心層過濾驅動（Interception，或自製 HID 過濾驅動）。Consumer 鍵（OK、返回、Home、選單、電源）沒有這個問題。
- 同一鍵雙擊兩下之間要約 0.3 秒，太快遙控器不會送第二下。
- 虛擬麥克風要另外安裝 VB-CABLE（不能隨包散佈）。
- 電腦進入睡眠或遙控器休眠後，重連由驅動自動處理，但 GET_CAPS 回覆常要 15 秒。

## 下一步

2. 內建離線語音辨識（whisper.cpp / faster-whisper / Vosk 擇一）。
3. 程式碼簽章。
5. 驗證 ATVV 的 MIC_CLOSE / MIC_EXTEND / 主機主動 MIC_OPEN，以及 1.0 模式的最長串流秒數。
6. 產品化：把虛擬音訊驅動與（可能的）鍵盤過濾驅動包進安裝程式，需要驅動簽章；VB-CABLE 轉散佈需授權，
   替代方案是微軟開源的 SysVAD / SimpleAudioSample 自行編譯簽章。
7. macOS 版：先解決配對，再移植驅動（CoreBluetooth + IOKit HID）。

## 重要背景

PlayRemote 是**自家公司的產品**，韌體可改。Apple 平台配對問題的解法以「改韌體」為主，
檢查清單見 `docs/APPLE_BLE_HID_CHECKLIST.md`。網路上查不到型號是正常的。

## 尚未取得的資訊

