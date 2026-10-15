#!/bin/bash
# 打包 macOS 版：PyInstaller → PlayRemote.app → PlayRemote-<版本>-macOS.dmg
# 用法: installer/build_mac.sh [版本]   (預設讀 pyproject.toml 的 version)
# 需要: .venv 已安裝 requirements.txt + pyinstaller
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-./.venv/bin/python}"
VERSION="${1:-$(grep -E '^version' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')}"
echo "== 版本 $VERSION"

rm -rf build dist
"$PY" -m PyInstaller --noconfirm --clean --name PlayRemote --windowed gui.py \
  --osx-bundle-identifier com.tvsoga.playremote \
  --collect-all bleak --collect-all sounddevice --collect-all numpy \
  --hidden-import AppKit --hidden-import Speech --hidden-import Quartz --hidden-import tomli \
  --hidden-import playremote_mac --hidden-import hid_mac --hidden-import macio --hidden-import coreaudio_mac \
  --hidden-import gestures --hidden-import stt --hidden-import probe --hidden-import audio \
  --add-data "config.example.mac.toml:." --add-data "config.example.toml:." --add-data "assets:assets"

APP=dist/PlayRemote.app
PLIST="$APP/Contents/Info.plist"
echo "== 補 Info.plist"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$VERSION" "$PLIST"
plutil -replace LSMinimumSystemVersion -string "13.0" "$PLIST"
plutil -replace NSMicrophoneUsageDescription -string "內建語音模式會從 PlayRemote 遙控器的麥克風錄音。" "$PLIST"
plutil -replace NSSpeechRecognitionUsageDescription -string "內建語音模式用 macOS 離線辨識把錄音轉成文字。" "$PLIST"
plutil -replace NSBluetoothAlwaysUsageDescription -string "用藍牙探測 PlayRemote 遙控器（選用）。" "$PLIST"
plutil -replace NSHumanReadableCopyright -string "MIT License" "$PLIST"
# ad-hoc 簽名（無 Developer ID 時），讓 TCC 權限能記住這個 App
codesign --force --deep --sign - "$APP"

echo "== 做 DMG"
DMG="dist/PlayRemote-$VERSION-macOS.dmg"
STAGE=dist/dmg
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/安裝說明.txt" <<'EOF'
PlayRemote for macOS（USB 接收器模式）

1. 把 PlayRemote 拖到 Applications。
2. 第一次開啟：在 Finder 對 PlayRemote 按右鍵 → 打開 → 打開（未經 Apple 簽署，只需做一次）。
   若仍被擋，到「系統設定 → 隱私權與安全性」最下方按「仍要打開」。
3. 依系統提示允許：輸入監控（讀遙控器按鍵）、輔助使用（送出熱鍵）。
   沒跳出提示時請到「系統設定 → 隱私權與安全性」手動把 PlayRemote 加進這兩項，然後重開 App。
4. 插上 PlayRemote 的 USB 接收器，視窗右上角會顯示「已連線」。
5. 語音：按住遙控器的語音鍵說話。預設會自動把系統麥克風切成 PlayRemote 並按住 Fn，
   搭配 SogaType 等「Fn 按住說話」的語音輸入法使用。

設定檔與記錄：~/Library/Application Support/PlayRemote/
EOF
hdiutil create -volname "PlayRemote $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
ls -la "$DMG"
echo "== 完成: $DMG"
