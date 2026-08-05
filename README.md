# Commit Tracker

Track **your own creative work** — the commits *you authored* in a shared monorepo —
into a local, deduplicated ledger with per-commit `.patch` files and monthly
Markdown reports.

It's built for the case where you need to log your creative output (e.g. for job
requirements) but the repo is a shared monorepo you can't add tooling to. This tool
lives **outside** the repo, reads a local clone, and stores everything locally. No
GitHub token, no API, nothing pushed to the monorepo.

---

## Compliance & scope (Bayer "Works" procedure)

This tool is a **personal aid**, not the official system. Read this before relying on it.

- **It does not replace Xpertis.** Under the Bayer procedure, Works must be recorded and
  reported in the Company's System (**Xpertis**) during the **Registration Period (1st–14th
  of the month following the Reference Period)**. This tool only helps you *find and
  document candidates* — it is not a submission and confers no tax treatment.
- **Output is a candidate list you must curate.** The tool lists every commit you authored.
  The procedure (Appendix No. 1) **excludes** many changes from being "Works" — e.g. minor
  changes that don't affect functionality, parameter/config/data changes, apparent-error
  fixes, added comments, reverts, and auto-generated output. Review each entry and drop
  what doesn't qualify before reporting. Each generated report carries this reminder at the
  top.
- **Never commit `data/` (contains company source).** The `.patch` files are real diffs
  from the monorepo — proprietary code. `data/` and `.env` are gitignored so they stay
  **local only**. Do not push them anywhere, and keep them out of any shared repo. Publish
  only the scripts, never the tracked output.
- **Declared Share of Creative Work is yours to set.** The tool does not compute it. Declare
  it in Xpertis in 10-point intervals, not exceeding your position's maximum (Appendix
  No. 8), reflecting your *actual* creative involvement for the period.
- **Co-authored work.** The tool attributes by single git author. Pair-programmed/co-created
  Works must be reported **separately by each co-creator** (Appendix No. 1 §2, Appendix
  No. 2 §7).

This is a workflow convenience, not legal or tax advice. Final qualification of Works, your
Declared Share, and acceptance rest with you, your accepting manager, and HR/Tax/Legal under
the procedure and its Documentation Quality Control.

---

## Why this exists / what "your work" means

The unit of work is **a commit whose git *author* is you** — not a PR you opened.
That distinction matters in a squash-and-merge monorepo:

- A PR you open from **someone else's** branch is authored by them → **excluded**.
- Your commits merged via **someone else's** PR are still authored by you → **included**
  (as long as their branch is still in your clone).

Commits are identified by their [`git patch-id`](https://git-scm.com/docs/git-patch-id),
so the same change is logged **once** even after rebases reassign SHAs. On top of that,
variants of the same PR (matched by the `(#1234)` suffix squash-merge adds) collapse
into a single entry.

---

## Requirements

- **macOS** (the scheduled runner uses `launchd`; the CLI itself is cross-platform).
- **Python 3.9+**
- **git**
- A **local clone** of the monorepo you want to track (kept reasonably up to date).

---

## Setup

```bash
# 1. Clone this tool (anywhere outside the monorepo)
git clone <your-private-repo-url> commit-tracker
cd commit-tracker

# 2. Create a virtualenv and install deps
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 3. Configure
cp .env.example .env
```

Edit `.env`:

```ini
# Absolute path to your local clone of the monorepo.
REPO_PATH=/Users/you/path-to-your-monorepo

# Your git author email(s), comma-separated. A commit counts as "yours"
# when its git author matches one of these.
MY_EMAILS=you@example.com
```

Find your author email(s) with:

```bash
git -C /path-to-your-monorepo log --author="Your Name" --pretty="%ae" | sort -u
```

`.env`, `.venv/`, and `data/` are gitignored, so your paths, emails, and tracked work
never get committed.

---

## Usage

All commands run through the venv's Python:

```bash
./.venv/bin/python commit_tracker.py <command> [options]
```

### `sync` — scan authored commits into the local store

```bash
# Default: work mode, fetches first, scans full history
./.venv/bin/python commit_tracker.py sync

# Only a window (author date, inclusive)
./.venv/bin/python commit_tracker.py sync --since 2026-06-01
./.venv/bin/python commit_tracker.py sync --since 2026-06-01 --until 2026-06-30
./.venv/bin/python commit_tracker.py sync --month 2026-06

# Skip the git fetch (scan the clone as-is, e.g. when offline)
./.venv/bin/python commit_tracker.py sync --no-fetch

# Wipe the table first (use when switching modes or doing a clean start)
./.venv/bin/python commit_tracker.py sync --reset --since 2026-06-01
```

`sync` is **idempotent** — running it repeatedly adds new work and re-confirms existing
entries by their stable key, so daily runs never create duplicates.

### `report` — generate monthly Markdown reports

```bash
./.venv/bin/python commit_tracker.py report            # all months
./.venv/bin/python commit_tracker.py report --month 2026-06
```

Writes one file per month to `data/reports/YYYY-MM.md`, each entry linking to its
`.patch` file.

### `list` — quick console listing

```bash
./.venv/bin/python commit_tracker.py list
./.venv/bin/python commit_tracker.py list --month 2026-07
./.venv/bin/python commit_tracker.py list --landed      # only work that landed
./.venv/bin/python commit_tracker.py list --unlanded    # only work not yet landed
```

`[L]` marks work that has landed on `origin/datafoundation` or `origin/master`.

---

## Grouping modes

Pick with `--mode`. Switching modes mixes two different groupings in one table, so
always pass `--reset` when you change it.

| Mode           | One entry per…                       | Best for                                  |
|----------------|--------------------------------------|-------------------------------------------|
| `work` (default) | PR (or `patch-id`)                 | "What shipped" — the deduped landed ledger |
| `day`          | feature branch × calendar day        | "Effort" — a day's edits as one combined diff |

- **`work`** is the most complete for history: landed squash commits survive even after
  their feature branch is pruned.
- **`day`** walks each `dev-datafoundation-*` feature branch in topological order and
  combines that day's commits into a single diff, globally deduped by `patch-id`. It only
  sees feature branches that still exist in your clone.

```bash
./.venv/bin/python commit_tracker.py sync --mode day --reset
```

> The landed/on-master reachability refs are hardcoded to `origin/datafoundation` and
> `origin/master`, and `day` mode looks for `dev-datafoundation-*` branch names. If your
> repo uses different names, adjust `LANDED_REFS` and `dev_branch_refs()` in
> `commit_tracker.py`.

---

## Clean start / historical backfill

There's no separate "backfill" step — `sync` always scans your entire local clone, so all
history still present in the clone is available every run. `--since/--until/--month` just
narrow it.

To wipe everything and rebuild from a chosen date:

```bash
# Remove generated artifacts (DB, patches, reports)
rm -rf data/patches data/reports data/tracker.db

# Rebuild the ledger from a start date up to today
./.venv/bin/python commit_tracker.py sync --reset --since 2026-06-01
./.venv/bin/python commit_tracker.py report
```

**Caveats for historical loads:**

- Filtering is by **author date**, so "since June" means work you *authored* since June,
  regardless of when it merged.
- Work whose branch was already **pruned** from your clone can't be recovered in `day`
  mode. In `work` mode, landed squashes on `datafoundation`/`master` persist and are still
  captured.

---

## Automatic daily runner (macOS `launchd`)

`run_sync.sh` is the wrapper the scheduler calls. It `cd`s into this folder, runs a sync,
regenerates reports, and appends output to `data/logs/sync.log`.

```bash
#!/bin/bash
./.venv/bin/python commit_tracker.py sync --since 2026-06-01 >> data/logs/sync.log 2>&1
./.venv/bin/python commit_tracker.py report >> data/logs/sync.log 2>&1
```

> The `--since 2026-06-01` in `run_sync.sh` pins where your ledger starts. Change it (or
> remove it) to match when you want your tracking to begin. Without it, a full-history
> scan would pull in everything back to your first commit.

### Install the schedule

`com.example.commit-tracker.plist` is a template that runs the sync **once a day at
18:00 local time**.

**Each engineer must personalize it** — it ships with a placeholder `Label` and
`/absolute/path/to/commit-tracker` paths. Copy it to your own name and edit:

- `Label` → a unique reverse-DNS id, e.g. `com.jdoe.commit-tracker`
- the `run_sync.sh` path under `ProgramArguments`
- the two log paths (`StandardOutPath` / `StandardErrorPath`)

```bash
# Make your own copy (replace jdoe with your id)
cp com.example.commit-tracker.plist com.jdoe.commit-tracker.plist
# ...edit the Label and the three /absolute/path/... entries...
```

Then install and load it:

```bash
chmod +x run_sync.sh
cp com.jdoe.commit-tracker.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jdoe.commit-tracker.plist

# Optional: run once now to verify
launchctl start com.jdoe.commit-tracker
launchctl list | grep commit-tracker
```

To disable it:

```bash
launchctl unload ~/Library/LaunchAgents/com.jdoe.commit-tracker.plist
```

> The daily fetch talks to your git remote, so your machine needs network/VPN access at
> run time. A failed fetch just means that day's new commits are picked up on the next
> successful run. If the Mac is asleep at 18:00, `launchd` runs the job at the next wake.

You can always run a manual update anytime with `./run_sync.sh`.

---

## Output layout

Everything the tool produces lives under `data/` (gitignored):

```
data/
├── tracker.db              # SQLite store (table: authored_work)
├── patches/
│   └── YYYYMM/             # one .patch per piece of work (git format-patch)
│       └── <slug>-<sha>.patch
├── reports/
│   └── YYYY-MM.md          # monthly Markdown summaries
└── logs/
    └── sync.log            # runner output
```

The SQLite `authored_work` table keys each entry by a stable `work_key`
(`pr:<number>`, `patch:<patch-id>`, or `day:<branch>:<date>`), which is what makes
repeated syncs idempotent.

---

## How deduplication works

1. **`patch-id`** collapses identical diffs, so rebase copies with different SHAs count once.
2. **PR number** (`(#1234)` in the squash-merge subject) merges every WIP/rebase/squash
   variant of one PR into a single entry (`work` mode).
3. **Reachability** against `origin/datafoundation` / `origin/master` marks each entry as
   landed or not.
4. In `day` mode, a **bracket-level `patch-id`** check drops days whose combined diff is
   identical to one already recorded (e.g. the same feature continued on a sibling branch).

---

## Troubleshooting

| Symptom | Fix |
|--------|-----|
| `REPO_PATH ... is not a git repository` | Point `REPO_PATH` at the folder containing `.git`. |
| `MY_EMAILS is not set` | Add at least one author email to `.env`. |
| `0 after date filter` | Check your `--since`/`--until` values; dates are `YYYY-MM-DD` and filter on **author** date. |
| Fetch hangs or fails | Ensure VPN/network is up, or run with `--no-fetch` to scan the clone as-is. |
| Missing recent work | Update your clone (`git fetch` in the monorepo) then re-sync. |
| Old/unwanted entries reappear | Add/adjust `--since` in `run_sync.sh`, then `sync --reset`. |

---

## Reusing this for yourself (checklist)

1. Set `REPO_PATH` and `MY_EMAILS` in `.env`.
2. If your repo doesn't use `datafoundation`/`master`/`dev-datafoundation-*`, adjust
   `LANDED_REFS` and `dev_branch_refs()` in `commit_tracker.py`.
3. Pick a start date and set it in `run_sync.sh` (`--since`).
4. Personalize the `.plist` (`Label`, paths) and install it.
5. Run an initial `sync --reset --since <date>` + `report`.
