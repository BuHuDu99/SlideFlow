#!/bin/zsh
set -eu
cd "${0:A:h}"

if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "Thiếu môi trường đóng gói. Hãy chạy: ./setup.command --dev"
  exit 1
fi

OUTPUT_DIR="${PWD}/dist"
APP_PATH="${OUTPUT_DIR}/SlideFlow.app"
DMG_PATH="${OUTPUT_DIR}/SlideFlow.dmg"
DMG_TEMP_PATH="${OUTPUT_DIR}/SlideFlow.tmp.dmg"
STAGING_DIR="$(mktemp -d "${TMPDIR:-/tmp}/slideflow-dmg.XXXXXX")"

cleanup() {
  rm -rf "${STAGING_DIR}"
  rm -f "${DMG_TEMP_PATH}"
}
trap cleanup EXIT

mkdir -p "${OUTPUT_DIR}"
rm -rf "${APP_PATH}" "${OUTPUT_DIR}/SlideFlow"
rm -f "${DMG_PATH}" "${DMG_TEMP_PATH}" "${OUTPUT_DIR}/.DS_Store"
export PYINSTALLER_CONFIG_DIR="${PWD}/.pyinstaller-cache"
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python scripts/make_icon.py
.venv/bin/python -m PyInstaller --noconfirm --clean SlideFlow.spec
mkdir -p build/chrome-host-dist
xcrun clang \
  -std=c11 \
  -O2 \
  -Wall \
  -Wextra \
  -Werror \
  -arch arm64 \
  -mmacosx-version-min=13.0 \
  chrome_native_host.c \
  -o build/chrome-host-dist/SlideFlowChromeHost
codesign --force --sign - build/chrome-host-dist/SlideFlowChromeHost
cp build/chrome-host-dist/SlideFlowChromeHost dist/SlideFlow.app/Contents/MacOS/SlideFlowChromeHost
xattr -cr dist/SlideFlow.app
codesign --force --deep --sign - dist/SlideFlow.app
codesign --verify --deep --strict dist/SlideFlow.app
# codesign and local security services can add provenance metadata while the
# bundle is assembled. Keep the developer build clean so Finder does not treat
# bundled Python.framework as a separately downloaded item.
xattr -cr dist/SlideFlow.app
# PyInstaller also leaves a duplicate standalone build and regenerable caches.
# Keep the signed .app as the only packaged result.
rm -r \
  dist/SlideFlow \
  build/SlideFlow \
  build/chrome-host-dist \
  .pyinstaller-cache/pyinstaller
rmdir build .pyinstaller-cache 2>/dev/null || true

# Build a self-contained installer disk image. The extension payload is kept
# separate from the root installer so Chrome can copy it to a persistent local
# directory before the DMG is ejected.
EXTENSION_PAYLOAD="${STAGING_DIR}/SlideFlow Chrome Extension"
mkdir -p "${EXTENSION_PAYLOAD}/icons"
ditto "${APP_PATH}" "${STAGING_DIR}/SlideFlow.app"
cp chrome_extension/manifest.json "${EXTENSION_PAYLOAD}/manifest.json"
cp chrome_extension/background.js "${EXTENSION_PAYLOAD}/background.js"
cp chrome_extension/icons/*.png "${EXTENSION_PAYLOAD}/icons/"
cp chrome_extension/install_host.command "${STAGING_DIR}/Cài tiện ích Chrome.command"
chmod 755 "${STAGING_DIR}/Cài tiện ích Chrome.command"
ln -s /Applications "${STAGING_DIR}/Applications"
printf '%s\n' \
  'CÀI SLIDEFLOW' \
  '' \
  '1. Kéo SlideFlow.app vào lối tắt Applications.' \
  '2. Nhấp đúp “Cài tiện ích Chrome.command”.' \
  '3. Trong Chrome, bật Developer mode, chọn Load unpacked và chọn thư mục Finder vừa mở.' \
  '' \
  'Trình cài tiện ích chép dữ liệu vào Application Support, nên có thể tháo DMG sau khi cài.' \
  > "${STAGING_DIR}/Hướng dẫn cài đặt.txt"

VERSION="$(plutil -extract CFBundleShortVersionString raw -o - "${APP_PATH}/Contents/Info.plist")"
hdiutil create \
  -volname "SlideFlow ${VERSION}" \
  -srcfolder "${STAGING_DIR}" \
  -format UDZO \
  -imagekey zlib-level=9 \
  -ov \
  "${DMG_TEMP_PATH}"
hdiutil verify "${DMG_TEMP_PATH}"
mv "${DMG_TEMP_PATH}" "${DMG_PATH}"

# The DMG is the only distributable. Remove the unpacked app and all caches
# created while testing or packaging.
rm -rf "${APP_PATH}" __pycache__ scripts/__pycache__ tests/__pycache__
rm -f .DS_Store "${OUTPUT_DIR}/.DS_Store"

echo "Built: ${DMG_PATH}"
