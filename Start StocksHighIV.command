#!/bin/bash
cd "$(dirname "$0")" || exit 1
# Finder may omit Homebrew and python.org installations from PATH.
launcher_python=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
  if [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    launcher_python="$candidate"
    break
  fi
done
if [ -z "$launcher_python" ]; then
  echo 'Install Python 3.11 or newer from https://www.python.org/downloads/ then launch again.'
  read -r -p 'Press Enter to close.'
  exit 1
fi
"$launcher_python" launch.py
if [ $? -ne 0 ]; then read -r -p 'Launch failed. Press Enter to close.'; fi
