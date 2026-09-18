#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Install Python 3.11 or newer from https://www.python.org/downloads/ then launch again.'
  read -r -p 'Press Enter to close.'
  exit 1
fi
python3 launch.py
if [ $? -ne 0 ]; then read -r -p 'Launch failed. Press Enter to close.'; fi
