# Mac Mini 除錯交接（voice → SogaType F18 不觸發）

給在 **Mac Mini 本機**接手的 Claude Code：先 `git pull`，這份是目前卡住的點與下一步。

## 現況（2026-11-18）
- 已發布 **v0.3.7**（已公證、含內建 BlackHole 自動安裝、熱鍵下拉）。按鍵、飛鼠、藍牙配對、閒置不收音都 OK。
- **卡住的點**：在這台 Mac Mini 上，「按住遙控器語音鍵 → 轉給 SogaType」**SogaType 沒有被觸發**。

## 已確認 OK（從 Mac Mini 的 driver.log）
- `語音鍵按下（藍牙 ATVV）`：語音鍵有偵測到。
- `麥克風：BlackHole 2ch → BlackHole 2ch`：預設輸入是 BlackHole（SogaType 會收到我們餵的音）。
- `放開：85 幀…`：ATVV 音訊有進來、有餵進 BlackHole。
- 使用者設定：PlayRemote 熱鍵 = **f18**（與 SogaType 的觸發鍵一致）、hotkey_style = hold；**輔助使用已授權**、重開過 app。
- 送鍵程式正確：`macio.parse_combo("f18").key == 79`（F18 的 macOS keycode），`key_down` 走
  `CGEventPost(kCGHIDEventTap, ...)`，HID 層級送出，不是不送。

→ 結論：**驅動有送 F18，但 SogaType 沒反應**。要查的是「送出的 synthetic F18 有沒有/為什麼沒觸發 SogaType」。

## 參考：之前在另一台（有內建鍵盤）用 **Fn** 時，voice→SogaType 是**成功**的。
差別是 **f18 vs fn** 以及**機器不同**。SogaType 不會過濾 synthetic（Fn 那次有效），所以 F18 理論上也該有效。

## 下一步（在 Mac Mini 本機直接測，不用一直發版）
專案在 `~/playremote`（或 `git clone https://github.com/stark640423/playremote`）。用 **Python 3.9** 的 venv：
```bash
cd ~/playremote
./.venv/bin/python -m pip install -r requirements.txt   # 若還沒裝
```

1. **隔離測「synthetic F18 會不會觸發 SogaType」**（不經遙控器/藍牙）：
   先讓 SogaType 開著、焦點放在可打字的地方，跑：
   ```bash
   ./.venv/bin/python -c "import macio,time; macio.key_down(macio.parse_combo('f18')); time.sleep(1.5); macio.key_up(macio.parse_combo('f18'))"
   ```
   - SogaType **有**被觸發 → 問題在遙控器→送鍵的流程（時序/執行緒），回去看驅動。
   - SogaType **沒**被觸發 → synthetic F18 本身就進不去 SogaType。再比對：把上面換成 `'fn'` 看 Fn 會不會觸發；查 SogaType 是用哪種方式監聽 F18（CGEventTap / NSEvent global monitor / RegisterEventHotKey），RegisterEventHotKey 對 synthetic 的行為要特別查。
   - 跑這個的那個「終端機／Claude Code」也要有**輔助使用**權限才送得出鍵。

2. **從原始碼跑驅動看即時 log**（新版會印送鍵那行）：
   ```bash
   ./.venv/bin/python playremote_mac.py --config "$HOME/Library/Application Support/PlayRemote/config.toml" -v
   ```
   按住語音鍵，看有沒有：`送熱鍵 f18（hold）給語音輸入 App；輔助使用: 已授權`。
   - 顯示「未授權」→ 權限沒真的生效（從原始碼跑時是「跑它的終端機」要有輔助使用）。
   - 顯示「已授權」但 SogaType 仍沒反應 → 同第 1 點的 synthetic 問題。

3. SogaType 原始碼若在這台（之前在另一台是 `~/voicetype`，`Sources/Hotkey.swift`/`Trigger.swift`）：
   看它怎麼監聽 F18，確認是否接受 synthetic 事件。

## 注意
- 不要把裝置 MAC 位址、API key、韌體 .bin、簽章憑證 commit 進這個開源 repo。
- 從原始碼跑的權限（輸入監控／輔助使用／藍牙）是綁在「跑它的那個程式」（終端機/Claude Code），和打包的 .app 是分開的。
