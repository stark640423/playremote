# 韌體修改交接（給 PC 那邊的工作階段）

更新：2026-10-15，Mac 工作階段整理。目標：**讓 PlayRemote 在 macOS / iOS 能用藍牙配對**。

先讀：`docs/APPLE_BLE_HID_CHECKLIST.md`（macOS 拒絕配對的原因與修改清單）、`NOTES.md`（GATT、ATVV、HID 格式）。

## 2026-10-15 更新：找到產品韌體，修補檔已產生

使用者在「下載項目」找到 `29A001烧录说明-在线烧录.rar`（**廠商資料，不進 repo**），已解壓到 `下載項目/29A001烧录说明-在线烧录/`，內含：

| 檔案 | 說明 |
|---|---|
| `富臣游戏手柄_202208291530/2.4g+ble-pc.bin` | **遙控器產品韌體**（371,216 bytes，2022-08-29，sha256 `37cbd95d72fc5f81…`）。bank0 @0x00000（BLE，0x23D68 bytes）、bank @0x40000（0x1AA10 bytes，推測 2.4G 模式）|
| `富臣游戏手柄_202208291530/dongle_28-pc.bin` | USB 接收器韌體（47,680 bytes）。不需要動 |
| `PlayRemote烧录操作说明-2022831.docx` | 量產燒錄說明（含照片） |
| `M02-在线烧录--Normal_download_tool_v8.5.2/` | Telink Normal download tool（Windows） |

### 已確認的廣播陣列（bank0）

| 位置 | 內容 |
|---|---|
| `0x219B0`（25 B）| ADV：`02 01 05` / `03 03 12 18` / `0B 09 "PlayRemote"` / `05 FF 5D 00 55 66` — **沒有 Appearance** |
| `0x219CC`（15 B）| KMSWand 信標：`02 01 05` / `0B FF 5D 00 FF "KMSWand"` |
| `0x219DC`（28 B）| 掃描回應：`03 03 12 18` / `03 19 80 01`（Appearance 0x0180）/ 名稱 / `05 FF 5D 00 55 88` / `00 00` |
| `0x2121A` | rodata：UUID `0x2A01` 常數，後面 `0x2121E` 為 `80 01`（推定 GATT GAP Appearance 值）|
| `0x21174`、`0x211AA` 附近 | HID 服務 1812 / Report Map 2A4B |

### 修補

```
python fwpatch.py 2.4g+ble-pc.bin 2.4g+ble-pc_mac-patched.bin
```

- ADV 的廠商資料（6 B）換成 Appearance `03 19 C1 03` + `00 00` 填充；掃描回應的 Appearance 0x0180 → 0x03C1。
- 共改 8 bytes，陣列長度不變；每個位置先比對原始位元組，不符就中止。
- `--gatt-appearance` 會一併改 `0x2121E`（推定位置），**第一輪先不要加**。
- 修補後檔案在 Mac 端已產生：`captures/firmware/remote_2.4g+ble_20220829_mac-patched.bin`（sha256 `edd817896974eb3d…`）。PC 端可自行用 fwpatch.py 從原檔重新產生，結果應相同。

### 燒錄設定（取自量產說明）

- 板上燒錄口 **J3：TX / GND / PRG / VCC_2V8**（新版板）。接 **GND、PRG（=SWS）、VCC**。舊版板（BT031F）是三針 GND / SWS / VCC。
- Normal download tool v8.5.2：MCU **8255**、Mode **EVK**、Addr **0x000000**、Size x Sector **128**。
  操作：Open 選 bin → 第 2 顆按鈕（連線/啟動）→ Download。
- ⚠ **Size 128 = 下載前清除整片 512 KB**，會一併清掉 Flash 裡每支遙控器的資料（MAC 位址 @0x76000、與 USB 接收器的配對資訊等）。
  工廠量產就是這樣燒，韌體應可自行處理，但**燒錄前必須先讀出整片備份**（BDT 或工具的讀取功能，0x0–0x80000），兩次比對一致才繼續。

### 建議順序

1. 讀出這支遙控器目前的韌體整片備份（`playremote_dump_1/2.bin`），`fwinspect.py --compare` 確認一致。
2. `fwinspect.py playremote_dump_1.bin`，確認 0x219B0 附近的廣播陣列與 2022-08-29 版**相同**
   （若不同，代表遙控器跑的是別的版本：改用 `fwpatch.py --force` 對 dump 修補，或回報 Mac 端重新分析）。
3. 先在**備用機**燒錄修補檔，Mac 上驗證（見下方「驗證」）。
4. 驗證通過才燒其他支；量產可改用 OTA（遙控器有 Telink OTA 服務）。
5. 若 macOS 仍不提供配對：再試 `--gatt-appearance`，或 `--appearance 0x03C2`（Mouse）。

### 驗證（Mac 端）

- 系統設定 → 藍牙 → 附近裝置：PlayRemote 圖示應從通用藍牙變成鍵盤，點選後進入「我的裝置」。
- Mac 有藍牙日誌描述檔，可查 `Finding image for "PlayRemote" … appeareance:961`（0x03C1 = 961）。
- `hidutil list` 出現 VID 0x2D01 / PID 0x5000；方向鍵能移動焦點。
- 同時確認 2.4G 模式（USB 接收器）仍正常；若接收器需要重新配對，記錄配對方式。

## 結論先講

- macOS 26 / iOS 對 PlayRemote **只建立 LE 連線、不發起配對**。原因在遙控器的**廣播內容**，Mac 端沒有任何繞過方法
  （第三方程式向 bluetoothd 報到時 `fProgrammaticPairing 0`，私有 PairingAgent SPI 也無效）。→ **只能改韌體**。
- 原始碼與原韌體工程師都找不到。公司有 **Telink 燒錄器**（使用者 2026-10-15 找到），所以走「讀出韌體 → 修補 → 寫回」。

## 硬體

| 項目 | 內容 |
|---|---|
| 主晶片 | **Telink TLSR8251**（原理圖 U2 標 M02R，QFN48；Flash 512 KB） |
| 板號 | `BT031F-TLS-001 V1.0`（2020/11/06）；原理圖標題欄 **KEHWIN**（方案公司，2022/06/25） |
| 燒錄口 | 板上三針 **GND / SWS（燒錄腳）/ VCC**；另有 4 針 J3 Debug |
| 其他 | ICM40608 陀螺儀、類比麥克風、IR 發射/學習、滾輪、2.4G/BLE 滑動開關、24 MHz 晶振 |
| USB 接收器 | 也是 Telink（認證韌體檔頭同為 KNLT） |

相關原始資料在使用者電腦的「下載項目」（**廠商資料，不要放進 repo**）：
`BLE丢包测试和EMI测试-20230413/`（含燒錄口接線照片的《TLSR8251认证指导说明.docx》、原理圖 `附件1.pdf`、Telink Non_Signaling_Test_Tool）、
`附件1playremote-2.pdf`（同一張原理圖，拿掉了 KEHWIN 標題）、`29A001方框图-20230110-遥控器.docx`（認證方框圖，29A001 可能是認證型號）。
資料夾裡的 `.bin`（`8258_Test_EMI_V0005.bin`、`TX_PB1_RX_PB0.bin`、`游戏手柄认证程序/*`）都是**認證測試韌體，絕對不要刷進產品**。

## 目前廣播（macOS bluetoothd 日誌實測）

- 位址 `A4:C1:38:44:BB:FB`（Public）。
- 配對模式（長按 Home + 返回）約 40 秒內送**可連線 HID 廣播**：
  - ADV：名稱 `PlayRemote`、16-bit UUID `0x1812`、廠商資料 `5D 00 55 66`（0x005D 只是韌體自填，非 Realtek）
  - Scan Response：再一次 `0x1812`、廠商資料 `5D 00 55 66 55 88`、**Appearance `0x0180`（Generic Remote Control）**
- 平時送**不可連線信標**，廠商資料 `5D 00 FF "KMSWand"`、Appearance 0。
- macOS 系統設定第一眼看到 ADV（無 Appearance）→ `appeareance:0 deviceType:0`，歸為通用藍牙裝置、圖示 Bluetooth.icns、
  點擊**完全不發出連線/配對請求**。

## 要改什麼（依可能性排序）

1. **把 Appearance 放進 ADV 主封包**，值改為 `0x03C1`（Keyboard）——macOS 才會歸類為輸入裝置並提供「連接」。
   ADV 上限 31 bytes：Flags(3) + UUID16 1812(4) + Appearance(4) + 名稱 PlayRemote(12) = 23，放得下；廠商資料可移到 Scan Response。
2. 若 1 之後能點「連接」但配對失敗：檢查 SMP 設定——需 **bonding**、IO capability `NoInputNoOutput`（Just Works）、MITM=0，
   HID 特徵值讀取/通知需加密。這些是程式碼裡的參數，二進位修補較難，可能要找 KEHWIN。
3. `KMSWand` 信標可保留（不影響），但若 macOS 因同位址兩種廣播而混淆，再考慮停用。

詳細規格見 `docs/APPLE_BLE_HID_CHECKLIST.md`。

## 步驟

### 1. 讀出並備份原始韌體（只讀，安全）

1. 遙控器**拔電池**，燒錄器的 GND / SWS / VCC 接到板上三針（位置見《TLSR8251认证指导说明.docx》照片「烧录脚」）。最好用**備用機**。
2. Telink BDT（Windows）：晶片選 8251（或 8258 系列）→ Connect。
3. Flash 讀取：起始位址 `0x0`、長度 `0x80000`（512 KB）→ 存成 `playremote_dump_1.bin`。**再讀一次**存成 `_2.bin`。
4. `python fwinspect.py playremote_dump_1.bin --compare playremote_dump_2.bin` → 必須「完全相同」。
5. 備份檔放到安全位置（不要進 repo；`captures/` 已被 .gitignore 排除，可放那裡）。
6. **在備份確認完整之前，不要按 BDT 的 Erase 或 Download。**

### 2. 分析

`python fwinspect.py playremote_dump_1.bin` 會列出：
- Telink 檔頭（0x08 `KNLT`、0x18 映像長度）與各 bank 位置（Telink 825x 的 OTA 通常是雙 bank：0x00000 與 0x20000/0x40000）。
- 廣播資料候選位置與解析結果（名稱、Appearance、UUID、廠商資料、Flags）。

要找的是 SDK 裡兩個 const 陣列：`tbl_advData[]`（ADV）與 `tbl_scanRsp[]`（Scan Response）。注意可能有兩份（兩個 bank），兩份都要改。

### 3. 修補

- 只改這兩個陣列的內容，**長度不變**（避免改到後面的程式碼），必要時用重新排列 AD 欄位達成。
- 若程式碼裡另有寫死的陣列長度（`bls_ll_setAdvData(tbl_advData, sizeof(...))`），ADV 長度一變就要連帶改那個常數——能不改長度最好。
- 檢查檔頭與校驗：Telink 825x 的 OTA 映像會檢查 CRC；用 BDT 直接寫 Flash 不經 OTA，通常不檢查。修補前後都用 `fwinspect.py` 對照。

### 4. 寫回與驗證

1. BDT 寫入修補後的整片（或只寫 bank 0）。先在備用機試。
2. 驗證：Mac 上「系統設定 → 藍牙 → 附近裝置」的 PlayRemote 圖示應變成鍵盤/遙控器，點選後進入「我的裝置」；
   `hidutil list` 出現 VID 0x2D01 / PID 0x5000；方向鍵能移動焦點。
3. 若要量產：做成 OTA 檔，用遙控器的 Telink OTA 服務（`00010203-0405-0607-0809-0a0b0c0d1912`）透過藍牙更新，不必拆機。
   OTA 協定在 Telink BLE SDK 文件裡有，之後可寫成 Mac/Windows 工具。

## Mac 端可協助的事

- 修補後要在 Mac 驗證時：Mac 已安裝 Apple 藍牙日誌描述檔，可用 `log show --predicate 'subsystem == "com.apple.bluetooth"'`
  看系統設定如何分類（`Finding image for "PlayRemote" … deviceType:… appeareance:…`）。
- 還沒做的對照實驗：錄 Mi Smart Mouse（可正常配對）的同類日誌，確認 macOS 26 的判斷條件。
