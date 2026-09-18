#!/bin/bash
# Daily run: scan 30-day IV for every stock, then rebuild the dashboard.
# Safe to re-run the same day; the scan resumes where it stopped.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/logs
.venv/bin/python -u -m highiv run "$@" >> "data/logs/$(date +%Y-%m-%d).log" 2>&1
