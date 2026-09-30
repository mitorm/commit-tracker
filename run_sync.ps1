# Wrapper used by the Windows Task Scheduler job. Runs a sync and appends to a log.
#
# Daily runs only need to REGISTER new work, not re-scan the whole backfill:
# SINCE is a rolling window = first day of the previous month. That always
# fully covers the month you're about to report plus the current month.
# Everything already in the database is kept (only `--reset` clears it), and
# `report` rebuilds from the full DB, so past months still appear in reports.
#
# The one-time historical backfill was done separately with an earlier date
# (e.g. `sync --reset --since 2026-06-01`); you don't repeat that here.
# Edit the sync flags below if you want a different mode (e.g. --mode day)
# or HTML reports (add --html to the report line).

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
New-Item -ItemType Directory -Force -Path "data\logs" | Out-Null

# First day of last month, e.g. on 2026-08-06 -> 2026-07-01.
$since = (Get-Date).AddMonths(-1).ToString("yyyy-MM-01")
$py    = ".\.venv\Scripts\python.exe"
$log   = "data\logs\sync.log"
$now   = { (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }

"=== sync started $(& $now) (since $since) ===" | Out-File -FilePath $log -Append -Encoding utf8
& $py commit_tracker.py sync --since $since *>> $log
& $py commit_tracker.py report *>> $log
# Keep a worklist of items still needing an AI description ready for the
# in-editor `/xpertis-descriptions` Copilot prompt (the AI step needs VS Code).
& $py commit_tracker.py describe *>> $log
"=== sync finished $(& $now) ===" | Out-File -FilePath $log -Append -Encoding utf8
