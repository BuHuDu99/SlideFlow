#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  ./setup.command
fi
exec .venv/bin/python main.py
