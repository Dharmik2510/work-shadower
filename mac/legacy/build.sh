#!/bin/bash
# Builds Dot.app (no Dock icon) from main.swift. Needs Xcode Command Line Tools.
set -e
cd "$(dirname "$0")"

APP=Dot.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

swiftc -O -swift-version 5 main.swift -o "$APP/Contents/MacOS/Dot"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Dot</string>
  <key>CFBundleExecutable</key><string>Dot</string>
  <key>CFBundleIdentifier</key><string>com.dharmik.dot</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP"
echo "Built $APP — run: open $APP"
