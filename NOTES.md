# PlayRemote 逆向筆記

更新：2026-10-14

## 裝置基本資料

| 項目 | 值 |
|---|---|
| 名稱 | PlayRemote（BLE 廣播名稱） |
| PnP ID | Vendor 0x2D01, Product 0x5000, Version 0 |
| MTU | 180 |
| 晶片 | **Telink TLSR8251**（內部認證資料與原理圖確認；板號 BT031F-TLS-001，方案 KEHWIN）。SWS 單線燒錄口：GND / SWS / VCC |
| HID | **有**（服務 1812，HID over GATT）。Windows 配對後自動建立鍵盤 / Consumer Control / 滑鼠 / 遊戲控制器四個 HID collection。macOS CoreBluetooth 和 Windows WinRT 都對 App 隱藏這個服務，所以 `probe.py dump` 看不到它（handle 15–52 整段缺） |

遙控器外觀：白色，上方圓形方向盤（中央 OK）；返回 / Home / 電源；數字 1–9；選單 / 0 / 退格；
滑鼠圖示鍵 / LED / 麥克風鍵；下方藍、綠、紅、黃四個彩色鍵。

## GATT 服務對照

### 標準服務

| Service | Char | 屬性 | 說明 |
|---|---|---|---|
| 180A Device Information | 2A50 | read | PnP ID |
| 180F Battery | 2A19 | read, notify | 電量百分比 |

### Telink OTA（韌體更新，不要碰）

| Service | Char | 屬性 |
|---|---|---|
| 00010203-0405-0607-0809-0a0b0c0d1912 | …2b12 | write-no-rsp, read |

### Google Android TV Voice（ATVV）— 麥克風

Google 公開規範的語音服務，Android TV 遙控器通用。

| Char | 屬性 | 角色 |
|---|---|---|
| ab5e0002-… | write-no-rsp, read | TX：主機 → 遙控器的指令（取得能力、開麥、關麥） |
| ab5e0003-… | read, notify | RX：遙控器 → 主機的音訊資料（ADPCM） |
| ab5e0004-… | read, notify | CTL：控制訊息（音訊開始/結束、能力回覆） |

#### 0.4 模式（預設）

- 按鍵、陀螺儀、滾輪操作期間，ATVV 的 notify 都沒有資料，只有麥克風有反應。
- 按麥克風鍵：CTL 送 `04 03 01 xx`（AUDIO_START，xx 是串流 id），音訊**不需要主機寫開麥指令就自動開始**。
- 偶爾出現 CTL `07`，規範裡沒有這個碼，推測是廠商自訂。
- 音訊幀：134 bytes = `seq(2,BE) id(1) prevSample(2,BE signed) stepIndex(1)` + 128 bytes IMA ADPCM，高 nibble 先解。
  每 30 ms 一幀 256 取樣 → 8 kHz。驗證方法：用幀表頭的 prevSample/stepIndex 對照上一幀解碼器的結束狀態，1118/1119 幀吻合。

#### 0.4 / 1.0 模式對照（Windows 實測，`atvv_probe.py`）

這隻遙控器**預設在 0.4 模式**，主機送了 1.0 格式的 GET_CAPS 之後才切到 1.0 模式。兩種模式行為差很多：

| | 0.4 模式（預設，主機什麼都不送） | 1.0 模式（主機先送 `0a 01 00 00 03 03`） |
|---|---|---|
| CAPS_RESP | `0b 00 04 00 01 00 86 00 14`：0.4 版、8 kHz ADPCM、幀 134、每筆 20 | `0b 01 00 03 03 00 80 01 00`：1.0 版、codecs 8k\|16k、互動模式 **0x03 = Hold-To-Talk**、幀 128 |
| 按鍵 | AUDIO_START `04 03 01 id`，**固定串流 18 秒**，放開不會停 | AUDIO_START `04 03 02 id`（reason HTT，codec 16 kHz） |
| 放開 | 沒反應，18 秒後才 AUDIO_STOP `00` | **立刻** AUDIO_STOP `00 02`（reason = HTT 放開）。按一下只送 8 幀 |
| 幀格式 | 134 bytes，自帶 seq/pred/step 表頭，8 kHz | **128 bytes 純 ADPCM，無表頭，16 kHz**（256 取樣 = 16 ms） |
| 解碼器狀態 | 每幀表頭重設 | 跨幀延續；每 20 幀送 AUDIO_SYNC `0a codec seq(2) pred(2) step(1)` 可重設 |
| 主機主動開麥 | 未測 | 未測（MIC_OPEN / MIC_CLOSE / MIC_EXTEND 都還沒驗證） |

所以**驅動程式連上後第一件事就是送 1.0 的 GET_CAPS**，語音鍵才會是「按住說話、放開就停」。

**控制通道在沒有音訊流動時常會延遲約 15 秒**：CAPS_RESP 固定如此，AUDIO_STOP 有時立刻到、有時放開後 15 秒才到。
原因未明（連線參數或 WinRT 端）。→ 驅動判斷「放開」不能只等 AUDIO_STOP，要同時用「超過 0.5 秒沒有新幀」當結束條件。

ATVV 1.0 指令位元組（整理自 ATVVoice 專案的實作，非官方 PDF）：

| 方向 | 訊息 | 位元組 |
|---|---|---|
| 主機→遙控器 | GET_CAPS | `0a` version(2,BE) reserved(1) codecs(1: bit0 8k, bit1 16k) 互動模式遮罩(1: bit0 PTT, bit1 HTT) |
| 主機→遙控器 | MIC_OPEN | `0c` codec(2,BE)（0.4）/ `0c` mode(1)（1.0） |
| 主機→遙控器 | MIC_CLOSE | `0d` stream_id(1)，`ff` = 任一 |
| 主機→遙控器 | MIC_EXTEND | `0e` stream_id(1)，1.0 才有，當 keepalive |
| 遙控器→主機 | AUDIO_START | `04` reason(1: 00 MIC_OPEN / 01 PTT / 03 HTT) codec(1) stream_id(1) |
| 遙控器→主機 | AUDIO_STOP | `00` reason(1: 00 MIC_CLOSE / 02 HTT 放開 / 04 新串流 / 08 逾時 / 10 通知關閉)；0.4 只有 `00` |
| 遙控器→主機 | AUDIO_SYNC | `0a` codec(1) seq(2,BE) pred(2,BE signed) step(1) |
| 遙控器→主機 | CAPS_RESP | `0b` version(2) codecs(1) 互動模式(1) 幀大小(2) extra(1) reserved(1) |
| 遙控器→主機 | MIC_OPEN_ERROR | `0c` code(2,BE)：0f01 codec 不對、0f02 遙控器未啟用、0f80 PTT/HTT 進行中 |

參考實作（同類專案，可借鏡）：

- Clay-Mic（Windows，Rust + Tauri，MIT）：ATVV 語音鍵 → whisper.cpp 離線辨識 → 打進前景視窗；用 Interception 驅動攔實體鍵。
- vibe-remote（Windows，Python + PyQt6，MIT）：Raw Input + WH_KEYBOARD_LL 分辨遙控器按鍵；WinRT BLE 收 ATVV。
- ATVVoice（Linux，Rust）：協定實作最完整，0.4/1.0 自動偵測、MIC_EXTEND keepalive。
- onn-remote-linux：指令位元組表與 Python 範例。

### 按鍵、陀螺儀、滾輪：HID over GATT（Windows 確認）

Windows 配對 PlayRemote 後，裝置管理員出現 `BTHLEDEVICE\{00001812-…}_DEV_VID&012D01_PID&5000_…`，
底下四個 HID collection：

| Collection | Windows 認成 | Usage Page / Usage | 報告格式（來自 preparsed data） |
|---|---|---|---|
| Col01 | HID Keyboard Device（kbdhid） | 0x01 / 0x06 Keyboard | 標準鍵盤報告；Raw Input 以掃描碼 + VKey 回報 |
| Col02 | Consumer control device | 0x0C / 0x01 | report ID 2，6 bytes；Consumer usage 陣列 0x001–0x3FF |
| Col03 | HID-compliant mouse（mouhid） | 0x01 / 0x02 Mouse | 標準滑鼠報告；空中滑鼠 / 滾輪走這裡 |
| Col04 | HID-compliant game controller | 0x01 / 0x05 Gamepad | report ID 196 (0xC4)，11 bytes；Button 1–15、Consumer 0x69、X/Y/Z/Rz 各 8 bit、Hat 4 bit；另有 output report ID 5 |

為什麼 `probe.py dump` 看不到 1812：macOS CoreBluetooth 和 Windows WinRT 都把 HID 服務藏起來不給 App 用，
dump 出來的 handle 直接從 13（PnP ID）跳到 53（電量），中間 15–52 就是 HID 服務。
Windows 上用 HID API 讀報告格式：直接 `CreateFile` 開這些 collection 會失敗，但 Raw Input 的 `RIDI_PREPARSEDDATA` 拿得到。

#### 實體按鍵 → HID 碼對照表

| 實體鍵 | Collection | 碼 | Windows 看到的 |
|---|---|---|---|
| ↑ ↓ ← → | 鍵盤 | scan 0x48/0x50/0x4B/0x4D（E0） | 方向鍵 |
| OK（中央鍵） | Consumer | 0x041 Menu Pick | Enter |
| 返回 | Consumer | 0x224 AC Back | 瀏覽器「上一頁」 |
| Home | Consumer | 0x223 AC Home | |
| 電源 | Consumer | 0x030 Power | **注意：Windows 可能會進入睡眠**，驅動預設擋掉 |
| 選單 | Consumer | 0x040 Menu | |
| 1–9、0 | 鍵盤 | scan 0x02–0x0A、0x0B | 數字鍵，會直接打進目前視窗 |
| 退格 | 鍵盤 | scan 0x0E | Backspace |
| 陀螺儀（空中滑鼠） | 滑鼠 | 相對 dx/dy，約 100 Hz | 系統游標直接動 |
| 滾輪 | 滑鼠 | `btnFlags=0x0400`，`btnData=±120` 一格 | 標準滾輪 |
| 麥克風鍵 | 無 HID 事件 | 只走 ATVV | 系統看不到這顆鍵，驅動自己接 |
| 彩色四鍵、滑鼠圖示鍵 | 無任何訊號 | HID 四個 collection、ATVV、自訂服務 `1920/2b00`、`0aa0/b002` 全部沒有通知（`probe.py watch` 實測） | 推測走紅外線（給電視）或遙控器本機功能（滑鼠鍵切換空中滑鼠），電腦端無法對應 |
| 遊戲控制器 collection | — | 從未出現 report ID 196 | 可能是韌體預留 |

其他實測到的行為：

- **同一鍵連按需要約 0.3 秒間隔**，太快第二下遙控器不會送（韌體去抖），所以雙擊判定窗設 600 ms。
- 長按時 Windows 會產生鍵盤自動重複（Raw Input 也會收到重複的 down）。

### 擋鍵：Windows 使用者層能做到什麼

**所有 HID 鍵都會同時被 Windows 當一般鍵盤滑鼠處理**；Raw Input 只能「看」不能「攔」。`rawinput.KeyBlocker` 用
`WH_KEYBOARD_LL` 低階鍵盤 hook 配合 Raw Input，反覆實測後確定的事實：

- **hook 比 WM_INPUT 先跑，而且被 hook 擋掉的鍵盤事件不會再產生 WM_INPUT**。所以鍵盤 collection 的鍵（數字、方向、退格）
  在 hook 當下無法分辨來源：**第一下一定會漏進目前視窗**，只有按住時的自動重複能擋（Raw Input 看到遙控器按下後登記 held）。
  這是使用者層的極限，要徹底擋掉只能靠核心層過濾驅動（Interception：個人免費、商用要授權；或自己寫 HID 過濾驅動並簽章）。
- **Consumer 鍵（返回、Home、選單、電源、OK）可以擋乾淨**：它們的 HID 報告走 Raw Input 的 HID 路徑，不受鍵盤 hook 影響，
  所以用「先擋後補」：hook 先擋下翻譯出來的 VK（注意這些翻譯鍵帶 `LLKHF_INJECTED` 旗標，不能用 injected 來分辨），
  Raw Input 說是遙控器就吞掉，是實體鍵盤就用 SendInput 原樣補送（`dwExtraInfo` 帶自訂標記，hook 放行）；逾時 50 ms 也補送。
  hook 和 Raw Input 先後順序不固定，兩邊都有 pre_consume / pre_pass 表互相配對。翻譯鍵的 Raw Input 事件 hDevice 是空的，
  要用「剛吞掉的 Consumer 鍵」清單去忽略它。

### 待確認的自訂服務

| Service | Char | 屬性 | 推測 |
|---|---|---|---|
| 00001920 | 2b00 | read, notify（20 bytes） | 遙控器 → 主機的資料流 |
| 00001920 | 2b01 | write, write-no-rsp | 主機 → 遙控器的指令 |
| 00000aa0-3c17-d293-8e48-14fe2e4da212 | b001 | read（2 bytes） | 狀態或版本 |
| 00000aa0-… | b002 | write, write-no-rsp, notify | 雙向 |

按一般鍵、彩色鍵、滑鼠鍵時這兩個服務都沒資料（2026-10-14 實測）。

## 連線行為

- 遙控器被系統連上後就不再廣播，掃描找不到。macOS 要用 `retrieveConnectedPeripherals` 取用系統端的連線；
  Windows 用 WinRT 列出已配對的 BLE 裝置找到位址，再用 `BLEDevice` 包起來交給 bleak（`probe.py` 都已處理）。
- 系統「忘記此裝置」後，遙控器端的配對不會清掉，雙方金鑰不符時連線請求會沒有回應（系統設定會一直轉圈）。
  需要清掉遙控器端配對（拔電池重置、重新進入配對模式）再重新配對。
- Windows：WinRT 和系統 HID 共用同一條連線，所以 bleak 訂閱 ATVV 麥克風時，按鍵仍然由系統當鍵盤滑鼠處理，兩邊同時運作。
- Windows：前一個 bleak 程序被強制結束後，下一次連線常會報 `Characteristic ab5e0004 was not found`（WinRT 的服務快取不完整），
  `BleakClient(..., winrt={"use_cached_services": False})` 可避免。
- macOS：目前配對卡在系統設定一直轉圈，HID 服務因此沒建立。Windows 一次就配對成功，所以是 macOS 端流程問題，不是協定問題。

### 2026-10-14 macOS 配對診斷紀錄

- 乾淨環境（Windows 藍牙關閉、遙控器拔電池後長按進配對模式）下，Mac 掃描結果：
  `PlayRemote`，-31 dBm，**廣播內列出服務 1812**，廠商資料 `0x005D`（Realtek）`55 66 55 88`。
  → macOS 認得出它是 HID 裝置，配對失敗的原因不在廣播內容。晶片很可能是 Realtek 而非 Telink。
- `probe.py pair`：CoreBluetooth 看得到的所有可讀特徵值都**不需要加密**即可讀取，
  因此無法靠讀取誘發系統配對對話框。HID 服務被 CoreBluetooth 隱藏，無法從 App 端觸發配對。
- **iPhone 實測（2026-10-14）**：iOS 設定點 PlayRemote 後顯示「已連線」，但列表項**沒有 ⓘ**（其他已配對裝置都有），
  按鍵在 iPhone 上無反應 → iOS 同樣只建立 LE 連線、未完成 bonding。確認是 Apple 平台共通的行為，與 Mac 環境無關。
  下一步只剩：Bluetooth Explorer 強制配對 / PacketLogger 看 SMP 原因；或從 PC 讀廣播 Appearance、`2A01`、`2A4A` 找 Apple 拒絕的條件。
- 結論：只剩系統設定的「連接」這一條路；失敗原因需要 PacketLogger 或 Apple 藍牙日誌描述檔才看得到。


## 虛擬麥克風（Windows）

- 需要虛擬音訊線（VB-CABLE）。`virtualmic.py` 把解碼後的 PCM 播到 CABLE Input，Windows 就在 CABLE Output 聽到；
  語音鍵按下時用 `IPolicyConfig`（pycaw）把預設麥克風切成 CABLE Output，放開後切回。
- CABLE Input 的 WASAPI 端只收 48 kHz，所以用裝置原生取樣率開，16 kHz 的音訊線性內插升頻；用 callback 緩衝 + 60 ms 預緩衝避免斷音。
- 主機板音效軟體附的「System Virtual Line」（MSI Sound Tune）實測不會把播放回送到錄音端，不能代替 VB-CABLE。

## 工具

```bash
python probe.py scan          # 掃描
python probe.py dump          # 列出 GATT
python probe.py watch         # 訂閱全部 notify 並記錄到 captures/
python audio.py               # 1.0 模式錄音，每次按住存一個 WAV
python atvv_probe.py --caps-v1 --wav   # ATVV 指令實驗
```

Windows 另有：

```powershell
python hidwatch.py --list     # 列出 PlayRemote 的 HID collection 與報告格式
python hidwatch.py            # 監看按鍵 / 滑鼠 / Consumer 事件，記錄到 captures/hid-*.log
python virtualmic.py          # 虛擬麥克風自我測試
```

`captures/` 不在 repo 裡（紀錄檔含錄音）。

## USB 2.4G 接收器（VID 0x0610 / PID 0x1000）的 HID 報告格式（2026-10-14 macOS 實測）

介面：USB Audio（麥克風 16 kHz）、HID page1/usage2 滑鼠、HID page1/usage5 遊戲控制器（內含鍵盤集合）、HID page12/usage1 Consumer、HID page 0xFF00 vendor。

| 報告 | 長度 | 內容 | 觸發 |
|---|---|---|---|
| `0xC4` | 11 | `c4 80 80 80 80 00…` 每 0.5 s 一次的閒置心跳（搖桿置中） | 無，忽略 |
| `0xC5` | 9 | 最後一個 byte 是**標準鍵盤 usage**：`4f`右 `50`左 `51`下 `52`上，`1e`–`27` = 1–9,0，`2a` 退格；放開送全 0 | 方向、數字、退格。**macOS 會當真鍵盤打字**，驅動需獨占裝置 |
| Consumer `id=2` | 3 | `02 LL HH`，usage 小端 16 bit；放開送 `02 00 00` | `0x41` OK、`0x224` 返回、`0x223` Home、`0x40` 選單、`0x30` 電源、`0xE9/0xEA` 音量＋/－、`0xCD` 播放暫停、`0x221` AC Search（待確認是否為語音鍵） |
| 滑鼠 `id=0` | 6 | `00 XL XH YL YH W`：X/Y 相對位移（小端 16 bit），W 滾輪 ±1 | 陀螺儀、滾輪。macOS 原生就能用 |
| vendor `0xFF00` | 32 | `0b 28 fe 00 0b 0X 28 fe…` / `0b 00 00 00 7b cc 0a…` 類似狀態與計數 | 非按鍵，用途未知 |

在 macOS 上這條路**不需要藍牙配對**；按鍵可用 IOHIDManager（`hidwatch_mac.py` 的做法）攔截，
語音輸入法直接選「PlayRemote」USB 麥克風。
