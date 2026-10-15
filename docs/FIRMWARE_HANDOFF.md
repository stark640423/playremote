# 韌體修改交接（給 PC 那邊的工作階段）

更新：2026-10-15，Mac 工作階段整理。目標：**讓 PlayRemote 在 macOS / iOS 能用藍牙配對**。

先讀：`docs/APPLE_BLE_HID_CHECKLIST.md`（macOS 拒絕配對的原因與修改清單）、`NOTES.md`（GATT、ATVV、HID 格式）。

## 2026-10-15 晚間更新（PC）：備用機已讀出並寫入最小修補，待 Mac 驗證

### 連線與讀寫方法

- 燒錄器（Telink，USB VID 248A / PID 5320，Windows 以 `usbprint` 驅動辨識，不用另裝驅動）接遙控器 J3：
  J3 印字由左到右 `TX / GND / PRG / VCC_2V8`，TX 空著；排線標籤 `− / PRG / +` 對 GND / PRG / VCC。
  J3 是空孔，排針直插常接觸不良（燈只在插入瞬間閃一下、`No Swire Slave Device`），要往一側斜壓或焊排針。
- 燒錄工具資料夾**要放純英文路徑**（例如 `C:\telink\`）：放在含簡體字的路徑下，繁中 Windows 會跳 `Create Process failed!`。
- 用命令列 `bat\tcdb.exe` 比 GUI 安全（GUI 的 Download 會先清除 Size x Sector 指定的範圍）：
  - 讀：`tcdb rf 0 -s 512k -o dump.txt`（位址為十六進位；輸出是**十六進位文字**，要自行轉回二進位）。
  - 寫單一區段：`tcdb wf 21000 -i sector.bin -b -e`（只清除並寫入檔案涵蓋的 4K）。
  - ⚠ `tcdb` **不加任何參數**時預設是 `wf -i boot.bin -m`（寫入），不要直接執行。

### 備用機的韌體

- 版本與 2022-08-29 原檔**不同**（bank0 0x1000 起約 1.4 萬 bytes 不同；0x40000 映像長度 0x1AB34 vs 原檔 0x1AA10），
  但廣播陣列 0x219B0–0x219F8 的位置與內容完全相同，所以 `fwpatch.py --force` 可直接套用在 dump 上。
- **0x0 第一個 4K 平常是空的（全 ff）**，兩種模式都一樣：
  晶片因此從 0x40000 開機；BLE 模式時，0x40000 的程式把 0x31C00 的 4K 副本寫回 0x0 再開機進 bank0，之後 0x0 又被清掉。
  （實測：BLE 開機過程中讀到 0x0 = 0x31C00 副本，完全一致；穩定後 0x0 又是全 ff。）
  0x36FFC–0x37000 有一個狀態位元組會隨模式改變（2.4G 時 0x97、BLE 時 0xE0）。
- 推論：**不要把 0x0 寫成完整韌體**，也不要用 GUI 清除整片後燒原廠合併檔，以免改變這套開機流程；修補只動 0x21000 那個區段。

### 已完成

1. BLE 模式整片讀兩次一致（sha256 `cefdf01ac3677313…`），2.4G 模式也讀了兩次；備份放在 `captures/`（不進 git）。
2. `fwpatch.py --minimal --force` 對 dump 修補：只改 0x219E2–0x219E3（掃描回應 Appearance 0x0180 → 0x03C1）。
3. 用 `tcdb wf 21000 ... -b -e` 只寫 0x21000 一個 4K 區段；讀回整片與預期完全相同，MAC 區（0x76000）未變。

### Mac 驗證結果與第二個修補（CCC 需加密）

- 只做 Appearance 修補後：macOS 認成鍵盤、會自動連線，但 `BTLEServer: Not creating HID device as the link is not encrypted`，
  30 秒後 `Service "Human Interface Device" has timed out`；系統設定「附近的裝置」一直轉圈圈。
- 原因：GATT 屬性表（**0x209B4**，`attribute_t` 每筆 24 bytes，共 80 個 handle，與 Mac 看到的 0x01–0x50 一致）裡，
  HID 特徵值的權限只有 READ/WRITE（0x01/0x03），讀寫都不會回「需要加密」，macOS 就不發起配對。
- Telink SDK 權限位元：READ 0x01、WRITE 0x02、AUTHOR 0x10、**ENCRYPT 0x20**、AUTHEN 0x40、SECURE_CONN 0x80
  （`ATT_PERMISSIONS_ENCRYPT_READ 0x21`、`ENCRYPT_WRITE 0x22`）；SDK 文件建議把通知用的 CCC 設成 ENCRYPT_WRITE 觸發配對。
- 修補：HID 服務內 5 個 CCC（handle 0x14、0x19、0x1D、0x24、0x28）的 perm 0x03 → **0x23**，
  位置 0x20B96、0x20C0E、0x20C6E、0x20D16、0x20D76，只寫 0x20000 一個 4K 區段。
- 結果（Mac 日誌）：`GATT is Asking to pair due to LE_ATT_ERROR_INSUFFICIENT_AUTHENTICATION startAutoPairing=1`
  → Just Works 配對視窗 → `Encryption is now enabled` → 產生永久金鑰（bonding）。
- 注意：配對前遙控器要在配對模式（長按 Home + 返回，藍燈閃）；macOS 26 系統設定的「連接」按鈕有時不出現，
  可用 bleak 連線一次（`BleakClient` 連上後等待）讓系統 HID 服務接手並觸發配對。
- 遙控器在配對模式時會自己清掉 0x74000 的舊配對紀錄，0x7D000 是它自己的狀態區，兩者都不用動。

### 下一步

- 確認按鍵可用、重開機後自動重連（日誌已見重連時以既有金鑰加密：`isPairing=0`、`Encryption is now enabled`）。
- 2.4G 模式（USB 接收器）**尚未驗證**（測試用 Mac 沒有 USB-A 孔）；兩個修補只動 bank0（0x20000、0x21000），
  0x40000 的 2.4G 映像未改，理論上不受影響，仍需實機確認。
- 整理成 `fwpatch.py` 的選項（Appearance + CCC 加密），再決定量產用燒錄或 OTA。

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

**第一輪建議用最小修補**（依 Mi Smart Mouse 對照，見 APPLE_BLE_HID_CHECKLIST.md）：

```
python fwpatch.py 2.4g+ble-pc.bin 2.4g+ble-pc_mac-minimal.bin --minimal
```

只改掃描回應 @0x219E2 的 2 bytes（Appearance 0x0180 → 0x03C1）。macOS 仍不提供連接時，再用完整修補：

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

### 之後改用藍牙 OTA 更新（目標：只有第一支用燒錄器）

遙控器韌體有 Telink OTA 服務 `00010203-0405-0607-0809-0a0b0c0d1912`（特徵值 `…2b12`，write-without-response，**不需配對即可寫入**，Mac 上已實測可連線讀寫），
所以理論上之後都能透過藍牙更新，Mac / Windows 的 PlayRemote App 都能當 OTA 主機（bleak）。燒錄器只用在第一支驗證。要先確認：

1. **OTA 寫到哪個 bank**：Telink 825x 預設把新韌體寫到 0x40000，但這份產品韌體的 0x40000 放的是 2.4G 模式映像。
   分析 dump 時要找出 OTA 目標位址（SDK 的 `ota_program_offset` / `bls_ota_set_fwSize_and_fwBootAddr` 設定）。
   若 OTA 會覆蓋 2.4G 映像，就只能更新 BLE 部分或必須連 2.4G 映像一起處理。
2. **OTA 映像格式**：產品燒錄檔 `2.4g+ble-pc.bin` 是兩個 bank 合併的整片，不是 OTA 檔。OTA 要用 bank0 那段（0x00000 起、長度看 0x18）
   並依 Telink 規則附加 CRC（SDK 工具 `tl_check_fw`），末 4 bytes 目前是 `0x001e007d`，不是 CRC，代表這份不是 OTA 格式。
3. **OTA 協定**：Telink BLE SDK 文件有（OTA start / data（index + 16 bytes + CRC16）/ end 命令），寫成 `ota.py` 後先在備用機試。
4. 失敗時 Telink 雙 bank 機制會保留舊韌體，正常情況不會變磚；但要求電池電量足夠。
USB 接收器的韌體（dongle_28-pc.bin）沒有 OTA，必要時才用燒錄器更新。

### 驗證（Mac 端）

- 系統設定 → 藍牙 → 附近裝置：PlayRemote 圖示應從通用藍牙變成鍵盤，點選後進入「我的裝置」。
- Mac 有藍牙日誌描述檔，可查 `Finding image for "PlayRemote" … appeareance:961`（0x03C1 = 961）。
- `hidutil list` 出現 VID 0x2D01 / PID 0x5000；方向鍵能移動焦點。
- 同時確認 2.4G 模式（USB 接收器）仍正常；若接收器需要重新配對，記錄配對方式。

## 2026-10-15 燒錄後 Mac 驗證：成功一半，問題已定位

> **已解決**（同日稍晚，PC 端）：HID CCC 改為需加密後 macOS 自動發起配對、建立 HID 裝置。見本文件開頭「Mac 驗證結果與第二個修補」。以下保留當時的分析。

在備用機燒錄修補韌體後，Mac 日誌：

**已成功**：macOS 認得它是鍵盤——
`Finding image … appearance:961 (Keyboard)`、`Aprc Keyboard`、`Detected keyboard LE HID`、`Compatible LE HID`、
讀到 VID 0x2D01 / PID 0x5000、HID / DeviceInfo / Battery 服務、Report Map、電量 97%。廣播修補完全有效。

**卡住**：`BTLEServer: Not creating HID device as the link is not encrypted`，HID 服務 30 秒逾時。
日誌序列：連線 → `No security keys found` → macOS 嘗試加密 → `encryptionStatusChangedCb status=4803`（失敗）
→ `Encryption is now disabled` → macOS **沒有發起 SMP 配對** → HID 逾時。

**原因**：標準 BLE HID 流程是「macOS 讀一個需要加密的 HID 特徵值 → 裝置回 Insufficient Authentication → 觸發配對」。
目前韌體的 HID 特徵值（Report / Report Map / HID Information 等）**沒有要求加密權限**，macOS 讀得到、不報錯、因而不發起配對。
→ 第二個韌體修改：**把 HID 服務特徵值的存取權限設為「需要加密」（authentication/encryption required）**。

### GATT 屬性表位置（已定位，bank0）

| UUID 常數 | 位置 | 被引用於 |
|---|---|---|
| 0x1812 (HID service) | 0x21174 | 0x20BE8 |
| 0x2A4B (Report Map) | 0x211AA | 0x20E0C |
| 0x2A01 (Appearance) | 0x2121A | 0x20A34 |

屬性表（attribute table）在 0x20A00–0x21000 一帶（每個 entry 指向 UUID 常數 + 權限/讀寫 callback）。Telink BLE SDK 的屬性表是
`attribute_t my_Attributes[]`，每個 entry 有 `attNum, uuidLen, attrLen, attrMaxLen, uuid*, pAttrValue*, w, r`，權限在 flags。
需要解出表結構，找到 HID 相關 entry 的權限欄位，改成需要加密（Telink 的 `ATT_PERMISSIONS_ENCRYPT_READ` 等）。
這是程式碼結構的修補，比廣播難；若難以靜態修補，這是請 KEHWIN 改一行的最小需求（「HID characteristics 要求 encryption」）。

（已完成：屬性表在 0x209B4，每筆 24 bytes，perm 在偏移 +2；實際修補見開頭。）

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
