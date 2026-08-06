#!/bin/bash
# Wrapper used by the launchd job. Runs a sync and appends to a log.
#
# Daily runs only need to REGISTER new work, not re-scan the whole backfill:
# SINCE is a rolling window = first day of the previous month. That always
# fully covers the month you're about to report plus the current month.
# Everything already in the database is kept (only `--reset` clears it), and
# `report` rebuilds from the full DB, so past months still appear in reports.
#
# The one-time historical backfill was done separately with an earlier date
# (e.g. `sync --reset --since 2026-06-01`); you don't repeat that here.
# Edit the sync flags below if you want a different mode (e.g. --mode day).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/logs

# First day of last month (BSD/macOS `date`), e.g. on 2026-08-06 -> 2026-07-01.
SINCE=$(date -v-1m +%Y-%m-01)

echo "=== sync started $(date -u +%Y-%m-%dT%H:%M:%SZ) (since ${SINCE}) ===" >> data/logs/sync.log
./.venv/bin/python commit_tracker.py sync --since "${SINCE}" >> data/logs/sync.log 2>&1
./.venv/bin/python commit_tracker.py report >> data/logs/sync.log 2>&1
echo "=== sync finished $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> data/logs/sync.log
