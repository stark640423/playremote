# playremote

Open-source driver for the **PlayRemote** Bluetooth LE voice remote (a Telink-based Android TV style remote).
Map every key to your own actions (launch an app, send a key combo, type text, with single / double / long press),
and use the voice key as hold-to-talk voice input: either into your own speech-to-text command, or as the hotkey
of another dictation app, optionally turning the remote's microphone into the computer's default microphone.

**Status:** Windows works today (command-line daemon). macOS has probing tools only; a macOS driver is planned.
Protocol notes (ATVV voice service, HID report formats, Windows input quirks) are in [NOTES.md](NOTES.md);
progress and roadmap in [STATUS.md](STATUS.md). Everything below is in Traditional Chinese.

---

把 BLE 語音遙控器「PlayRemote」接到電腦：每個鍵自訂單擊／雙擊／長按動作，語音鍵按住說話。

## 功能（Windows）

- 自動連線、斷線重連、顯示電量。
- 每個鍵可設三種動作：啟動程式、送組合鍵、打字。OK／返回／Home／選單／電源可以完全擋掉原本的系統動作。
- 語音鍵按住說話：
  - `hotkey` 模式：變成其他語音輸入 App 的熱鍵（按住式或切換式），例如 SogaType 或 Windows 內建語音輸入（Win+H）。
    搭配 VB-CABLE 可以把遙控器的麥克風變成電腦的預設麥克風，那個 App 直接錄到遙控器的聲音。
  - `builtin` 模式：放開後把 WAV 交給你設定的辨識指令（例如 whisper.cpp），結果打進目前視窗。
  - `none`：只存 WAV。

## 安裝（Windows）

1. 在「設定 → 藍牙與裝置」配對 PlayRemote（依說明書進入配對模式）。配對後它會被 Windows 當成鍵盤／滑鼠。
2. 到 [Releases](https://github.com/stark640423/playremote/releases) 下載 `PlayRemote-Setup-<版本>.exe` 執行即可
   （裝到使用者資料夾，不需要系統管理員；也有免安裝的 `PlayRemote-portable-<版本>.zip`）。
   安裝程式沒有簽章，第一次執行若出現 SmartScreen 警告，按「其他資訊 → 仍要執行」。
3. 開始功能表的「PlayRemote」開啟設定視窗：每個鍵可設單擊／雙擊／長按動作，語音鍵設定在右上，按「儲存並套用」即生效。
   關閉視窗後程式縮到系統匣繼續跑；安裝時可勾選「開機時自動啟動」（啟動時只顯示系統匣）。

### 從原始碼執行

安裝 Python 3.11 以上，然後：

```powershell
git clone https://github.com/stark640423/playremote.git
cd playremote
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.toml config.toml
.venv\Scripts\python playremote.py -v
```

第一次先用 `-v` 看每個鍵的事件，確認對應沒問題後拿掉。

想讓其他語音 App 錄到遙控器的聲音：安裝 [VB-CABLE](https://vb-audio.com/Cable/)，在 `config.toml` 的 `[voice.virtual_mic]` 設 `enabled = true`。

### 自己打包

```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --clean --name PlayRemote --windowed gui.py --collect-all bleak --collect-submodules winrt --collect-binaries winrt --collect-data winrt --collect-all pycaw --collect-all comtypes --collect-all sounddevice --hidden-import numpy --add-data "config.example.toml;." --add-data "assets;assets"
iscc installer\playremote.iss      # 需要 Inno Setup 6，產出 dist\PlayRemote-Setup-<版本>.exe
```

推 `v*` 標籤到 GitHub 會由 Actions 自動打包並建立 Release（`.github/workflows/release.yml`）。

`config.toml` 的格式與說明見 [config.example.toml](config.example.toml)。驅動會自動找已配對、名稱是 PlayRemote 的裝置；有多隻時在 `[remote] address` 填 MAC。

## 已知限制

- 數字鍵、方向鍵、退格的**第一下會漏進目前視窗**（例如按 1 開程式時，游標所在的輸入框會先出現一個 1）。這是 Windows 使用者層 API 的極限，
  詳見 NOTES.md；要徹底擋掉需要核心層過濾驅動。
- 同一鍵雙擊兩下之間要約 0.3 秒，太快遙控器不會送第二下。
- 下方四個彩色鍵和滑鼠圖示鍵目前沒有對應（它們不走 HID）。

## 工具

| 檔案 | 用途 |
|---|---|
| `gui.py` | Windows 設定介面 + 系統匣（主程式）：遙控器圖、每鍵三種動作、語音鍵設定、連線與電量 |
| `playremote.py` | Windows 驅動核心（也可以單獨以命令列執行）：連線、按鍵對應、語音鍵、虛擬麥克風 |
| `config_store.py` | `config.toml` 讀寫 |
| `rawinput.py` | Windows Raw Input 監聽 + 低階鍵盤 hook（只處理遙控器的鍵） |
| `winio.py` | Windows 送組合鍵 / Unicode 打字 / 啟動程式 |
| `virtualmic.py` | 遙控器音訊 → VB-CABLE → 切換預設麥克風 |
| `audio.py` | ATVV 1.0 麥克風：按住錄音、IMA ADPCM 解碼、`AtvvMic` 類別 |
| `probe.py` | BLE 探測：`scan`、`dump`、`watch`、`poke` |
| `atvv_probe.py` | ATVV 指令實驗：GET_CAPS / MIC_OPEN / MIC_CLOSE / MIC_EXTEND，記錄原始幀 |
| `hidwatch.py` | Windows：監看 PlayRemote 的 HID 事件，對照實體鍵與 HID 碼 |
| `stt.py` | macOS 內建離線語音辨識（WAV → 文字） |

## macOS

目前只有探測與錄音工具可以跑（需要給終端機「藍牙」權限，`stt.py` 另需「語音辨識」權限）。
配對問題尚未解決，驅動本體還沒有 macOS 版，見 STATUS.md。

## 授權

MIT，見 [LICENSE](LICENSE)。VB-CABLE 是 VB-Audio 的軟體，請自行安裝。
