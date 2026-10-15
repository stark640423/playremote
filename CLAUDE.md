# playremote

BLE 語音遙控器「PlayRemote」的開源驅動程式（Windows 可用，macOS 規劃中），授權 MIT。

開始任何工作前先讀：

1. `STATUS.md`：目標、進度、已知限制、下一步。
2. `docs/FIRMWARE_HANDOFF.md`：**目前的主要待辦**——讀出 TLSR8251 韌體、修補廣播資料，讓 macOS/iOS 能藍牙配對。
3. `NOTES.md`：GATT 服務對照、ATVV 0.4/1.0 行為、HID 報告格式與按鍵對照表、Windows 擋鍵的限制。

程式都是 Python（依賴見 `requirements.txt` / `pyproject.toml`）：

- `playremote.py` 驅動本體（Windows）；`rawinput.py`、`winio.py`、`virtualmic.py` 是它的 Windows 層。
- `audio.py` 的 `AtvvMic` 是跨平台的麥克風核心；`probe.py` 負責找裝置與連線。
- `atvv_probe.py`、`hidwatch.py` 是實驗工具。

慣例：

- 文件用繁體中文；README 開頭保留一段英文簡介。
- `captures/` 和 `config.toml` 不進 git（紀錄檔含錄音、設定含個人路徑）。
- Windows 上用 `py -m venv .venv` 建環境；不要把 Mac 的 `.venv` 複製過來用。
- 不要在 repo 裡寫入任何個人裝置位址、API key 或內部專案路徑。
