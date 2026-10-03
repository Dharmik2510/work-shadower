#!/bin/bash
# Builds Dot.app from the Swift package. Needs Xcode 15+ (or its Command Line Tools) on macOS 13+.
#   ./build.sh            → release build, ad-hoc signed
#   SIGN_ID="Developer ID Application: Your Co (TEAMID)" ./build.sh   → signed for distribution
set -euo pipefail
cd "$(dirname "$0")"

VERSION=${VERSION:-0.1.0}
BUILD=${BUILD:-$(date +%Y%m%d%H%M)}
BUNDLE_ID=${BUNDLE_ID:-com.workshadower.dot}
SIGN_ID=${SIGN_ID:--}
APP=Dot.app

# Tests need XCTest, which ships with full Xcode (not the Command Line Tools).
if [ "${SKIP_TESTS:-0}" != "1" ] && [ -d "$(xcode-select -p 2>/dev/null)/Platforms" ]; then
  echo "→ Running core tests"
  swift test
else
  echo "→ Skipping tests (needs full Xcode)"
fi

# UNIVERSAL=1 builds arm64 + x86_64 (needs full Xcode, not just Command Line Tools).
ARCH_FLAGS=()
if [ "${UNIVERSAL:-0}" = "1" ]; then ARCH_FLAGS=(--arch arm64 --arch x86_64); fi
echo "→ Building release ${ARCH_FLAGS[*]:-(native)}"
swift build -c release ${ARCH_FLAGS[@]+"${ARCH_FLAGS[@]}"}
BIN=$(swift build -c release ${ARCH_FLAGS[@]+"${ARCH_FLAGS[@]}"} --show-bin-path)/Dot

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Dot"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Dot</string>
  <key>CFBundleDisplayName</key><string>Work Shadower</string>
  <key>CFBundleExecutable</key><string>Dot</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>${BUILD}</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleURLName</key><string>${BUNDLE_ID}.link</string>
      <key>CFBundleURLSchemes</key><array><string>workshadower</string></array>
    </dict>
  </array>
  <key>NSAppleEventsUsageDescription</key>
  <string>Work Shadower opens and switches apps when it runs a skill for you.</string>
  <key>NSAccessibilityUsageDescription</key>
  <string>Work Shadower reads button and field names while you record, and presses them when it runs a skill for you.</string>
  <key>NSScreenCaptureUsageDescription</key>
  <string>Work Shadower takes a few screenshots of the front window at key moments of a recording.</string>
</dict>
</plist>
PLIST

echo "→ Signing ($SIGN_ID)"
if [ "$SIGN_ID" = "-" ]; then
  codesign --force --sign - "$APP"
else
  codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$APP"
fi

echo
echo "Built $APP ($VERSION / $BUILD)."
echo "Run:  open $APP"
echo "Then grant Accessibility + Input Monitoring (and optionally Screen Recording) when asked."
echo "Note: macOS ties permissions to the signature. With ad-hoc signing, re-grant after every rebuild."
