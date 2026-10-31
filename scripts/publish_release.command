#!/bin/zsh
set -eu
cd "${0:A:h}/.."
VERSION="$(.venv/bin/python -c 'from app_version import APP_VERSION; print(APP_VERSION)')"
# Build first, then make this exact commit available as a stable release.
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || { echo 'Hãy commit mã nguồn trước khi phát hành.'; exit 1; }
./build_app.command
gh release create "v${VERSION}" dist/SlideFlow.dmg \
  --repo BuHuDu99/SlideFlow --target "$(git rev-parse HEAD)" \
  --title "SlideFlow ${VERSION}" --generate-notes --latest
