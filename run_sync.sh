#!/bin/bash
# Wrapper used by the launchd job. Runs a sync and appends to a log.
# --since pins the ledger's start; new work is added on top of it each run.
# Edit the sync flags below if you want a different mode (e.g. --mode day).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/logs
echo "=== sync started $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> data/logs/sync.log
./.venv/bin/python commit_tracker.py sync --since 2026-06-01 >> data/logs/sync.log 2>&1
./.venv/bin/python commit_tracker.py report >> data/logs/sync.log 2>&1
echo "=== sync finished $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> data/logs/sync.log
