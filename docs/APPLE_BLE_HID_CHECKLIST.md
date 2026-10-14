# Apple 平台（iOS / macOS）BLE HID 配對檢查清單 — 給 PlayRemote 韌體端

更新：2026-10-14

## 觀察到的現象

**2026-10-14 藍牙日誌（已裝 Apple 描述檔）結論**：macOS 認得它是 BLEHID、廣播含 1812；
系統設定點擊時**完全沒有發出連線或配對請求**，問題卡在設定頁的裝置分類，不是 SMP。
原因是 Appearance 只在掃描回應、且值為 0x0180。另有一個同位址的非連線型信標（廠商資料 `5D 00 FF "KMSWand"`，Appearance 0）在非配對模式時送出。

- macOS 26 與 iOS 都**只建立 LE 連線、不發起配對**：系統設定列出 PlayRemote，但用通用藍牙圖示、沒有 ⓘ、按鍵無反應。
- 同一隻遙控器在 Windows 一次配對成功，識別為 GATT compliant HID device。
- 乾淨環境掃描：廣播含服務 UUID `1812`，廠商資料 `0x005D`（Realtek）`55 66 55 88`，無法從 Mac 端看到是否含 Appearance。
- 旁邊的小米滑鼠在同一個畫面有滑鼠圖示 → Apple 用廣播的 Appearance 決定圖示與裝置類型。

結論：Apple 沒有把 PlayRemote 當成「可配對的輸入裝置」。下列各項請逐一核對。標 ★ 的是最可能的原因。

## 1. 廣播（Advertising / Scan Response）

| 項目 | 要求 | PlayRemote 現況 |
|---|---|---|
| ★ **Appearance (AD type 0x19)** | 必須放在 **ADV 主封包**（不是掃描回應），且建議用 `0x03C1`（Keyboard）而非 `0x0180`（Generic Remote Control）。新版 macOS 設定頁只用主封包決定裝置類型 | **有，但在掃描回應裡，值 0x0180**。macOS 日誌：主封包到時 `appeareance:0 deviceType:0`，掃描回應到時 `appeareance:384` 但 `deviceType` 不更新 → 歸為通用裝置，不提供連接 |
| 16-bit Service UUID 列表含 `0x1812` | 必須 | 有 |
| Flags | LE General Discoverable + BR/EDR Not Supported（`0x06`） | 待確認 |
| Complete/Shortened Local Name | 建議有 | 有（PlayRemote） |
| 廣播間隔 | 配對模式 20–30 ms；平時可拉長 | 待確認 |

## 2. GAP 服務（0x1800）

| 項目 | 要求 |
|---|---|
| `2A00` Device Name | 必須 |
| ★ `2A01` Appearance | 必須，且與廣播一致 |
| `2A04` Peripheral Preferred Connection Parameters | 建議有，值要符合第 4 節 |

## 3. 配對 / 安全（SMP）

| 項目 | 要求 |
|---|---|
| ★ 必須支援 **bonding**（Authentication Requirements 的 Bonding 旗標為 1） | Apple 只會和能綁定的 HID 裝置配對 |
| IO Capability | 無按鍵顯示的遙控器用 `NoInputNoOutput`（Just Works）。**不要**宣告 KeyboardOnly 卻無法輸入 passkey，Apple 會要求 MITM 而失敗 |
| MITM | 與 IO capability 一致：NoInputNoOutput 時 MITM = 0 |
| LE Secure Connections | 建議支援（SC = 1）；Apple 支援 legacy，但 SC 較穩 |
| 金鑰分發 | 至少分發 LTK（EncKey）；建議 IRK 也分發（Apple 用 RPA） |
| Security Request | 連線後遙控器可主動送 SMP Security Request 要求加密，能加速 Apple 發起配對 |
| ★ **HID 特徵值的存取權限** | Report、Report Map、HID Information 等必須設為「需要加密」（Encryption required）。若設成不需要加密，Apple 不會發起配對、也不會把它當 HID 使用 |
| 綁定數量 | 建議至少 2–3 組，避免換主機就必須清除 |

## 4. 連線參數（Apple Accessory Design Guidelines）

遙控器透過 L2CAP Connection Parameter Update 要求的值必須同時符合：

- Interval Min ≥ 15 ms（HID 可用 11.25 ms）
- Interval Min + 15 ms ≤ Interval Max（或 Interval Min = Interval Max = 15 ms）
- Interval Max × (Slave Latency + 1) ≤ 2 s
- Slave Latency ≤ 30
- Supervision Timeout 2 s – 6 s，且 > Interval Max × (Slave Latency + 1) × 3

不符合的請求會被 Apple 拒絕，嚴重時斷線。

## 5. HID 服務（0x1812）

| 項目 | 要求 |
|---|---|
| `2A4A` HID Information | 必須。`bcdHID`、`bCountryCode`、Flags（RemoteWake / NormallyConnectable） |
| `2A4B` Report Map | 必須。與 USB 接收器用的描述子一致即可（macOS 已能解析 USB 版本） |
| `2A4C` HID Control Point | 必須 |
| `2A4E` Protocol Mode | 必須 |
| `2A4D` Report ×N | 每個都要有 `2908` Report Reference 描述子 |
| Boot Keyboard / Boot Mouse | 可選 |
| 上述特徵值 | 讀取與通知皆需加密（見第 3 節） |

## 6. 其他

| 項目 | 要求 |
|---|---|
| Device Information `2A50` PnP ID | 必須（已有：Vendor 0x2D01 / Product 0x5000） |
| 位址 | 公開位址（Realtek OUI A4:C1:38）可；若用隨機靜態位址，最高兩位元須為 `11` |
| 連上後 15–30 秒內若主機未加密，可主動送 Security Request | 建議 |

## 驗證方式

1. 改完韌體後，在 Mac 上 `./.venv/bin/python probe.py scan`，廣播應出現 Appearance（bleak 在 macOS 看不到此欄位，請改用 Windows 或 Android nRF Connect 確認）。
2. macOS 系統設定 → 藍牙 → 附近裝置應以鍵盤/遙控器圖示顯示，點選後進入「我的裝置」。
3. `hidutil list` 看得到 VID 0x2D01 / PID 0x5000，按方向鍵游標會動。
4. 若仍失敗：安裝 Apple 的 Bluetooth 日誌描述檔後用 PacketLogger 錄 SMP，看 Pairing Failed 的 reason code。
