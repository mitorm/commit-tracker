# Commit Tracker

**In one sentence:** it reads a project's Git history, finds the code *you personally
wrote* each month, and turns it into a clean monthly report — so you can easily show
your creative work.

## Who is this for?

Anyone who needs a record of the code they wrote in a **shared repository** — for
example, to report creative work for tax/HR reasons — without installing anything into
that shared repository.

## How it works (short version)

1. You point it at a **copy of the repo on your computer** and give it your **email**.
2. It reads the Git history and keeps only the commits **you** authored.
3. It removes noise and duplicates (rebases, and big "release" commits that are really
   the whole team's work).
4. It writes an easy-to-read **monthly report** and saves each change as a `.patch` file.

Everything runs and stays **on your computer**. Nothing is uploaded, and nothing is added
to the shared repo.

## Quick start (about 5 minutes)

```bash
# 1. Get the tool and go into its folder
git clone <your-private-repo-url> commit-tracker
cd commit-tracker

# 2. Install it (one time)
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 3. Tell it where your repo is and who you are
cp .env.example .env
#    now open .env and set two things:
#      REPO_PATH  = the folder on your computer that contains the repo
#      MY_EMAILS  = the email you commit with

# 4. Build your report (change the date to when you want to start tracking)
./.venv/bin/python commit_tracker.py sync --reset --since 2026-06-01
./.venv/bin/python commit_tracker.py report
```

Now open `data/reports/` — there's **one file per month**. That's it.

> **Don't know your Git email?** Run this (with your name):
> `git -C /path/to/repo log --author="Your Name" --pretty="%ae" | sort -u`

The rest of this README explains everything in more detail: the compliance rules it
follows, every command and option, how to schedule it to run daily, and how to adapt it
to a repo that isn't set up like ours.

---

## Compliance & scope (Bayer "Works" procedure)

This tool is a **personal aid**, not the official system. Read this before relying on it.

- **It does not replace Xpertis.** Under the Bayer procedure, Works must be recorded and
  reported in the Company's System (**Xpertis**) during the **Registration Period (1st–14th
  of the month following the Reference Period)**. This tool only helps you *find and
  document candidates* — it is not a submission and confers no tax treatment.
- **Release/integration rollups are auto-excluded.** Your workflow ends with
  `datafoundation → master` release PRs (`itfoundation release 1.x`, `deployment`,
  `hotfix`). GitHub squashes each into one commit **authored by you** (you raised the PR)
  whose diff is the **whole team's** work — not your creative contribution. The tool
  detects these (body enumerates many sub-PRs, or an explicit release/deployment/hotfix
  subject) and keeps them **out of your ledger**, flagged and listed in
  `data/reports/excluded.md`. Inspect them with `list --rollups`. Genuine feature PRs
  (0–1 sub-PRs) are unaffected. Tune the threshold via `ROLLUP_MIN_SUBPRS` in
  `commit_tracker.py`.
- **Rebase conflict-resolution artifacts.** When you rebase and resolve conflicts, hunks
  reconciling others' changes can be folded into a commit you authored. This can't be
  detected perfectly; each item's `.patch` is the commit's own diff against its parent
  (the cleanest available view), and `day`/feature-branch granularity avoids the big
  squashes entirely. Review individual patches before reporting.
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

## What counts as "your work"

The tool counts a change as yours when **you are the git *author*** of the commit — not
when you simply opened the pull request. In a squash-and-merge repo that matters:

- You open a PR from **someone else's** work → **not counted** (they authored it).
- Your commits ship inside **someone else's** PR → **counted** (you authored them).

It also cleans things up automatically so you don't get duplicates or noise:

- The **same change is counted once**, even if a rebase gave it new commit IDs.
- All variants of one PR are **merged into a single entry**.
- Big **release / deployment commits** (which bundle the whole team's work) are **left
  out** of your report — see *Compliance & scope*.

> Technical note: identity is based on [`git patch-id`](https://git-scm.com/docs/git-patch-id),
> and PR variants are matched by the `(#1234)` suffix that squash-merge adds.

---

## Requirements

- **macOS** (the scheduled runner uses `launchd`; the CLI itself is cross-platform).
- **Python 3.9+**
- **git**
- A **local clone** of the monorepo you want to track (kept reasonably up to date).

---

## Setup (detailed)

This is the longer version of the Quick start above, with more explanation.

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

Writes one file per month to `data/reports/YYYY-MM.md`, plus `data/reports/excluded.md`
(the release/integration rollups kept out of the ledger — see Compliance & scope).

Each monthly report is built to be reported from **without opening the patches**:

- a **summary table** — date, commit, PR, file count, `+/-`, an **assessment**
  (`creative` vs `review`), and any **co-author**;
- a **per-item, per-file `+/-` breakdown**, so you can pick exactly which files are your
  Work when filling in Xpertis;
- **`review`** marks changes that Appendix No. 1 §3 likely excludes from Works (reverts,
  parameter/config/id tweaks, apparent-error fixes, very small changes) — advisory only,
  the final call is yours;
- **co-authored** items name the other human; their squashed diff mixes in that person's
  work, so report only your own files/contribution.

The `.patch` files themselves contain **only your commit's diff**. Rollups get no patch;
the only patches that include someone else's lines are the co-authored squashes (git keeps
no per-line authorship, so those can't be split — use the per-file table to choose yours).

### `list` — quick console listing

```bash
./.venv/bin/python commit_tracker.py list
./.venv/bin/python commit_tracker.py list --month 2026-07
./.venv/bin/python commit_tracker.py list --landed      # only work that landed
./.venv/bin/python commit_tracker.py list --unlanded    # only work not yet landed
./.venv/bin/python commit_tracker.py list --rollups     # only the excluded rollups
```

Each line carries a 3-flag prefix `[L~&]`: **L** = landed on `origin/datafoundation` or
`origin/master`, **~** = likely not a Work (review), **&** = co-authored.

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
SINCE=$(date -v-1m +%Y-%m-01)   # first day of last month, e.g. 2026-08-06 -> 2026-07-01
./.venv/bin/python commit_tracker.py sync --since "$SINCE" >> data/logs/sync.log 2>&1
./.venv/bin/python commit_tracker.py report >> data/logs/sync.log 2>&1
```

### What period of commits is used?

There are **two different things** here — don't confuse them:

- **Fetch = everything.** `sync` first runs `git fetch --all --prune`, which pulls **all**
  new history from the remote (incrementally). It is **never** limited by date.
- **`--since` = what gets written to your ledger.** After fetching, the tool keeps only
  commits **authored on/after** that date.

The daily runner uses a **rolling window: the first day of the previous month**
(`date -v-1m +%Y-%m-01`). So each run only *registers recent* work (the month you're about
to report, plus the current one) instead of re-scanning many months. This is safe because:

- Your database **keeps everything already stored** — narrowing `--since` never deletes
  anything (only `--reset` does), and `report` rebuilds from the **full DB**, so every past
  month still appears in the reports.
- Running daily means any commit authored this month is captured while it's inside the
  window.

The **one-time historical backfill** is a separate, manual step with an earlier date — see
[Clean start / historical backfill](#clean-start--historical-backfill). You run that once;
the daily job does **not** repeat it. Never put `--reset` in `run_sync.sh` (that would wipe
the backfill). To change where tracking begins, edit the `SINCE` line in `run_sync.sh`.

### Install the schedule

`com.example.commit-tracker.plist` is a template that runs the sync **once a day at
18:00 local time**.

**Each engineer must personalize it** — it ships with a placeholder `Label` and
`/absolute/path/to/commit-tracker` paths. Copy it to your own name and edit:

- `Label` → a unique reverse-DNS id, e.g. `com.jdoe.commit-tracker`
- the `run_sync.sh` path under `ProgramArguments`
- the two log paths (`StandardOutPath` / `StandardErrorPath`) → use **real absolute paths**,
  e.g. `/Users/you/tools/data/logs/launchd.out.log`

```bash
# Make your own copy (replace jdoe with your id)
cp com.example.commit-tracker.plist com.jdoe.commit-tracker.plist
# ...edit the Label and the three /absolute/path/... entries...
```

Then install and load it. **Create the two folders first** — `~/Library/LaunchAgents`
usually doesn't exist yet, and `launchd` opens the log files *before* running your script,
so `data/logs` must already exist too:

```bash
chmod +x run_sync.sh
mkdir -p ~/Library/LaunchAgents          # create it if it doesn't exist yet
mkdir -p data/logs                       # launchd writes its logs here

cp com.jdoe.commit-tracker.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jdoe.commit-tracker.plist

# Optional: run once now to verify
launchctl start com.jdoe.commit-tracker
launchctl list | grep commit-tracker     # a line with your label = loaded; 2nd column 0 = OK
```

Check it actually ran:

```bash
tail -n 30 data/logs/sync.log            # look for "Done (work mode)" and the written reports
```

To disable it:

```bash
launchctl unload ~/Library/LaunchAgents/com.jdoe.commit-tracker.plist
```

> If you edit the plist later, re-apply with `launchctl unload …` then `launchctl load …`.

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
│   ├── YYYY-MM.md          # monthly Markdown summaries (rollups excluded)
│   └── excluded.md         # release/integration rollups kept out of the ledger
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
