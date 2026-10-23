#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
if [[ -n "${1:-}" ]]; then
  APP_PATH="$1"
elif [[ -d "/Applications/SlideFlow.app" ]]; then
  APP_PATH="/Applications/SlideFlow.app"
else
  APP_PATH="${SCRIPT_DIR}/dist/SlideFlow.app"
fi
INFO_PLIST="${APP_PATH}/Contents/Info.plist"

if [[ ! -d "${APP_PATH}" || ! -f "${INFO_PLIST}" ]]; then
  echo "Không tìm thấy SlideFlow.app tại: ${APP_PATH}"
  exit 1
fi

BUNDLE_ID="$(plutil -extract CFBundleIdentifier raw -o - "${INFO_PLIST}")"
if [[ "${BUNDLE_ID}" != "com.buihuuduc.slideflow" ]]; then
  echo "Dừng lại: đây không phải bundle SlideFlow được mong đợi."
  exit 1
fi

# Verify the known bundle before removing browser quarantine metadata. This
# script never disables Gatekeeper and never changes global security settings.
codesign --verify --deep --strict "${APP_PATH}"
xattr -dr com.apple.quarantine "${APP_PATH}" 2>/dev/null || true
codesign --verify --deep --strict "${APP_PATH}"

echo "SlideFlow đã được xác minh và chuẩn bị để mở trên máy này."
