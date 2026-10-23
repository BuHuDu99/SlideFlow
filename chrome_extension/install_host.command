#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
EXTENSION_SOURCE_DIR="${SLIDEFLOW_EXTENSION_SOURCE_DIR:-${SCRIPT_DIR}/SlideFlow Chrome Extension}"
if [[ ! -f "${EXTENSION_SOURCE_DIR}/manifest.json" ]]; then
  EXTENSION_SOURCE_DIR="${SCRIPT_DIR}"
fi

if [[ -n "${1:-}" ]]; then
  APP_PATH="$1"
elif [[ -d "/Applications/SlideFlow.app" ]]; then
  APP_PATH="/Applications/SlideFlow.app"
elif [[ -d "${HOME}/Applications/SlideFlow.app" ]]; then
  APP_PATH="${HOME}/Applications/SlideFlow.app"
elif [[ -d "${SCRIPT_DIR}/SlideFlow.app" ]]; then
  echo "Hãy kéo SlideFlow.app vào thư mục Applications trước, sau đó chạy lại trình cài này."
  /usr/bin/open "/Applications" 2>/dev/null || true
  exit 1
else
  APP_PATH="${SCRIPT_DIR:h}/dist/SlideFlow.app"
fi

HOST_PATH="${APP_PATH}/Contents/MacOS/SlideFlowChromeHost"
BRIDGE_DIR="${SLIDEFLOW_BRIDGE_DIR:-${HOME}/Library/Application Support/SlideFlow/ChromeBridge}"
INSTALLED_HOST_PATH="${BRIDGE_DIR}/SlideFlowChromeHost"
APP_PATH_FILE="${BRIDGE_DIR}/slideflow-app-path.txt"
MANIFEST_DIR="${SLIDEFLOW_NATIVE_HOST_DIR:-${HOME}/Library/Application Support/Google/Chrome/NativeMessagingHosts}"
MANIFEST_PATH="${MANIFEST_DIR}/com.buihuuduc.slideflow.json"
INSTALLED_EXTENSION_DIR="${SLIDEFLOW_EXTENSION_INSTALL_DIR:-${HOME}/Library/Application Support/SlideFlow/ChromeExtension}"
EXTENSION_ID="lgebdjdlladiamggnnnebcjccadfbcfa"

if [[ ! -x "${HOST_PATH}" ]]; then
  echo "Không tìm thấy cầu nối tại: ${HOST_PATH}"
  echo "Hãy build SlideFlow hoặc truyền đường dẫn SlideFlow.app vào lệnh này."
  exit 1
fi

for required_file in manifest.json background.js; do
  if [[ ! -f "${EXTENSION_SOURCE_DIR}/${required_file}" ]]; then
    echo "Thiếu dữ liệu extension: ${EXTENSION_SOURCE_DIR}/${required_file}"
    exit 1
  fi
done
if [[ ! -d "${EXTENSION_SOURCE_DIR}/icons" ]]; then
  echo "Thiếu thư mục biểu tượng extension: ${EXTENSION_SOURCE_DIR}/icons"
  exit 1
fi

APP_PATH="${APP_PATH:A}"
HOST_PATH="${APP_PATH}/Contents/MacOS/SlideFlowChromeHost"
BUNDLE_ID="$(plutil -extract CFBundleIdentifier raw -o - "${APP_PATH}/Contents/Info.plist")"
if [[ "${BUNDLE_ID}" != "com.buihuuduc.slideflow" ]]; then
  echo "Dừng lại: đây không phải bundle SlideFlow được mong đợi."
  exit 1
fi

# Verify the known app before removing only its browser quarantine metadata.
# The bridge is copied outside the .app so Chrome never executes a nested app
# component and therefore cannot cause Gatekeeper to inspect Python.framework.
codesign --verify --deep --strict "${APP_PATH}"
xattr -dr com.apple.quarantine "${APP_PATH}" 2>/dev/null || true
mkdir -p "${BRIDGE_DIR}" "${MANIFEST_DIR}"
cp -f "${HOST_PATH}" "${INSTALLED_HOST_PATH}"
chmod 700 "${BRIDGE_DIR}" "${INSTALLED_HOST_PATH}"
xattr -c "${INSTALLED_HOST_PATH}"
codesign --verify --strict "${INSTALLED_HOST_PATH}"
printf '%s\n' "${APP_PATH}" > "${APP_PATH_FILE}"
chmod 600 "${APP_PATH_FILE}"

ESCAPED_HOST_PATH="${INSTALLED_HOST_PATH//\\/\\\\}"
ESCAPED_HOST_PATH="${ESCAPED_HOST_PATH//\"/\\\"}"
printf '%s\n' \
  '{' \
  '  "name": "com.buihuuduc.slideflow",' \
  '  "description": "Send the active YouTube tab to SlideFlow",' \
  "  \"path\": \"${ESCAPED_HOST_PATH}\"," \
  '  "type": "stdio",' \
  '  "allowed_origins": [' \
  "    \"chrome-extension://${EXTENSION_ID}/\"" \
  '  ]' \
  '}' > "${MANIFEST_PATH}"
chmod 600 "${MANIFEST_PATH}"

# Keep the unpacked extension in a permanent location. Loading it directly
# from a mounted DMG would break as soon as the disk image is ejected.
mkdir -p "${INSTALLED_EXTENSION_DIR}"
rm -f "${INSTALLED_EXTENSION_DIR}/manifest.json" "${INSTALLED_EXTENSION_DIR}/background.js"
rm -rf "${INSTALLED_EXTENSION_DIR}/icons"
cp "${EXTENSION_SOURCE_DIR}/manifest.json" "${INSTALLED_EXTENSION_DIR}/manifest.json"
cp "${EXTENSION_SOURCE_DIR}/background.js" "${INSTALLED_EXTENSION_DIR}/background.js"
cp -R "${EXTENSION_SOURCE_DIR}/icons" "${INSTALLED_EXTENSION_DIR}/icons"
chmod -R u+rwX,go-rwx "${INSTALLED_EXTENSION_DIR}"

echo "Đã đăng ký cầu nối Chrome: ${MANIFEST_PATH}"
echo "Cầu nối độc lập: ${INSTALLED_HOST_PATH}"
echo "Extension ID: ${EXTENSION_ID}"
echo "Tiếp theo: mở chrome://extensions, bật Developer mode và Load unpacked thư mục:"
echo "${INSTALLED_EXTENSION_DIR}"

if [[ "${SLIDEFLOW_NONINTERACTIVE:-0}" != "1" ]]; then
  /usr/bin/open -a "Google Chrome" "chrome://extensions" 2>/dev/null || true
  /usr/bin/open "${INSTALLED_EXTENSION_DIR}" 2>/dev/null || true
  echo
  echo "Trong Chrome, chọn Load unpacked rồi chọn thư mục vừa được mở trong Finder."
  read "?Nhấn Enter để đóng cửa sổ này..."
fi
