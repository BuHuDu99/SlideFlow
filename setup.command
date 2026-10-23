#!/bin/zsh
set -eu

cd "${0:A:h}"

MODE="${1:-}"
if [[ -n "${MODE}" && "${MODE}" != "--dev" ]]; then
  echo "Cách dùng: ./setup.command [--dev]"
  exit 2
fi

if command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
  PYTHON_VERSION="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ "${PYTHON_VERSION}" != "3.12" ]]; then
    echo "SlideFlow cần Python 3.12; phiên bản hiện có là ${PYTHON_VERSION}."
    echo "Có thể cài bằng Homebrew: brew install python@3.12"
    exit 1
  fi
else
  echo "Không tìm thấy Python 3.12. Có thể cài bằng Homebrew: brew install python@3.12"
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  "${PYTHON_BIN}" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
if [[ "${MODE}" == "--dev" ]]; then
  .venv/bin/python -m pip install -r requirements-dev.txt
else
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "Môi trường SlideFlow đã sẵn sàng."
