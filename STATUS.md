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
| 設定介面 | 未開始。目標是像 SayAll 的按鍵映射頁：遙控器圖、每鍵三格動作、語音鍵選項、連線與電量 |
| 安裝程式 | **完成**：PyInstaller + Inno Setup（`installer/playremote.iss`），per-user 安裝、可選開機自啟；GitHub Actions 推 `v*` 標籤自動建 Release。未簽章 |
| 系統匣 | 未開始，目前是命令列視窗 |
| macOS 版 | 只有探測工具（`probe.py`、`audio.py`、`stt.py`）。配對卡在系統設定一直轉圈，待解 |
| 彩色四鍵、滑鼠圖示鍵 | 不走 HID，尚未查出走哪個服務 |

## 已知限制

- **數字鍵、方向鍵、退格的第一下會漏進目前視窗**。這是 Windows 使用者層 API 的極限（鍵盤 hook 當下分不出來源），
  要徹底擋掉需要核心層過濾驅動（Interception，或自製 HID 過濾驅動）。Consumer 鍵（OK、返回、Home、選單、電源）沒有這個問題。
- 同一鍵雙擊兩下之間要約 0.3 秒，太快遙控器不會送第二下。
- 虛擬麥克風要另外安裝 VB-CABLE（不能隨包散佈）。
- 電腦進入睡眠或遙控器休眠後，重連由驅動自動處理，但 GET_CAPS 回覆常要 15 秒。

## 下一步

1. 設定介面（技術選型：Python + PySide6，或改用 C#/.NET）。
2. 內建離線語音辨識（whisper.cpp / faster-whisper / Vosk 擇一）。
3. 系統匣圖示（關掉命令列視窗）、程式碼簽章。
4. 彩色鍵與滑鼠鍵：用 `probe.py watch` 看自訂服務。
5. 驗證 ATVV 的 MIC_CLOSE / MIC_EXTEND / 主機主動 MIC_OPEN，以及 1.0 模式的最長串流秒數。
6. 產品化：把虛擬音訊驅動與（可能的）鍵盤過濾驅動包進安裝程式，需要驅動簽章；VB-CABLE 轉散佈需授權，
   替代方案是微軟開源的 SysVAD / SimpleAudioSample 自行編譯簽章。
7. macOS 版：先解決配對，再移植驅動（CoreBluetooth + IOKit HID）。

## 尚未取得的資訊

- PlayRemote 的來源：背面標籤或附屬機上盒型號。查不到任何叫 PlayRemote 的產品。
