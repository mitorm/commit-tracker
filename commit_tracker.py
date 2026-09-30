"""Track my own creative work: commits I authored, across every branch.

The unit of work is a *commit I wrote* (git author == me), identified by its
`git patch-id` so the same change is logged once even after rebases reassign
SHAs. This is deliberately NOT PR-based: a PR I open from someone else's branch
is authored by them (excluded), and my commits merged via someone else's PR are
still authored by me (included), as long as their branch is still in the clone.

Source of truth is a local clone -- no GitHub token required.

Two grouping modes:
    work (default) -- one entry per PR (or patch-id); the deduped "what shipped".
    day            -- one combined diff per feature branch per day; several edits
                      to the same objects on a day become a single creative work.
Use --reset when switching modes so the two don't mix in one table.

Release/integration rollups (the datafoundation->master promotion squashes you
author only because you raised the release PR) are detected and *excluded* from
the ledger: their diff is the whole team's squashed work, not your own creative
contribution. They are kept in the DB flagged as rollups and listed in
`data/reports/excluded.md`; see them with `list --rollups`.

Each ledger item is also annotated to speed up Xpertis reporting:
    - a per-file +/- breakdown, so you can pick which files are your Work
      without opening the patch;
    - the commit message body as a ready-made description of the work;
    - clickable PR/commit links (derived from the `origin` remote URL) in
      reports; `report --html` also writes a styled, printable evidence file;
    - an advisory creative-vs-"review" assessment (trivial/parameter/revert
      changes that Appendix No. 1 §3 excludes from Works are flagged "review");
    - co-authors (real other humans), since squashed co-authored PRs mix in a
      teammate's work -- report only your own contribution.

Usage:
    python commit_tracker.py sync                       # work mode
    python commit_tracker.py sync --mode day --reset    # per-branch, per-day
    python commit_tracker.py sync --since 2026-06-01 --until 2026-06-30
    python commit_tracker.py sync --month 2026-06
    python commit_tracker.py report [--month 2026-06] [--html]
    python commit_tracker.py monthly [--month 2026-06]  # sync + report + export
    python commit_tracker.py describe [--month 2026-06]        # export Copilot worklist
    python commit_tracker.py describe --import FILE            # import descriptions
    python commit_tracker.py list [--month 2026-06] [--rollups]
"""

import argparse
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PATCHES_DIR = DATA_DIR / "patches"
REPORTS_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "tracker.db"

# Git's canonical empty tree, used as the base when a commit has no parent.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class ConfigError(RuntimeError):
    pass


class GitError(RuntimeError):
    pass


def load_config():
    load_dotenv(BASE_DIR / ".env")
    repo_path = os.getenv("REPO_PATH", "").strip()
    if not repo_path:
        raise ConfigError("REPO_PATH is not set. Copy .env.example to .env and set it.")
    repo = Path(repo_path).expanduser()
    if not (repo / ".git").exists():
        raise ConfigError("REPO_PATH '{}' is not a git repository.".format(repo))
    emails = [e.strip() for e in os.getenv("MY_EMAILS", "").split(",") if e.strip()]
    if not emails:
        raise ConfigError("MY_EMAILS is not set. Add at least one author email.")
    return {"repo": repo, "emails": emails}


def run_git(repo, args, check=True):
    proc = subprocess.run(
        ["git", "-C", str(repo)] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and proc.returncode != 0:
        raise GitError(
            "git {} failed:\n{}".format(" ".join(args), proc.stderr.strip())
        )
    return proc.stdout


def author_filter_args(emails):
    args = []
    for email in emails:
        # Anchor to the full email so a substring can't match another author.
        args.append("--author=" + re.escape(email))
    args.append("--regexp-ignore-case")
    return args


def fetch(repo):
    print("Fetching latest refs (git fetch --all --prune) ...")
    run_git(repo, ["fetch", "--all", "--prune"])


def collect_commits(repo, emails):
    """Return {sha: {authored_at, subject, insertions, deletions, files}}."""
    sep = "\x1f"
    # Header line marked with a sentinel, followed by --numstat rows.
    log_format = "@@@%H{sep}%aI{sep}%s".format(sep=sep)
    out = run_git(
        repo,
        ["log", "--all", "--no-merges"]
        + author_filter_args(emails)
        + ["--numstat", "--pretty=tformat:" + log_format],
    )
    commits = {}
    current = None
    for line in out.splitlines():
        if line.startswith("@@@"):
            parts = line[3:].split(sep)
            if len(parts) != 3:
                current = None
                continue
            sha, authored_at, subject = parts
            current = {
                "sha": sha,
                "authored_at": authored_at,
                "subject": subject,
                "insertions": 0,
                "deletions": 0,
                "files": 0,
                "paths": [],
            }
            commits[sha] = current
        elif line.strip() and current is not None:
            cols = line.split("\t")
            if len(cols) >= 3:
                add, delete, path = cols[0], cols[1], cols[2]
                current["files"] += 1
                current["insertions"] += int(add) if add.isdigit() else 0
                current["deletions"] += int(delete) if delete.isdigit() else 0
                current["paths"].append(path)
    return commits


def filter_commits_by_date(commits, since=None, until=None, month=None):
    """Keep commits whose author date falls in the requested window.

    ISO timestamps sort lexically, so plain string comparison on the date
    prefix is correct and sidesteps git's `--since` approxidate quirks.
    """
    if month:
        return {s: c for s, c in commits.items() if c["authored_at"][:7] == month}
    if since is None and until is None:
        return commits
    out = {}
    for sha, meta in commits.items():
        day = meta["authored_at"][:10]
        if since and day < since:
            continue
        if until and day > until:
            continue
        out[sha] = meta
    return out


def normalize_label(ref):
    return ref.split("/")[-1]


def dev_branch_refs(repo):
    out = run_git(
        repo,
        ["for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"],
    )
    refs = [r for r in out.splitlines() if "dev-datafoundation" in r]
    # One ref per feature branch, preferring the remote (origin) copy.
    by_label = {}
    for ref in refs:
        label = normalize_label(ref)
        if label not in by_label or ref.startswith("origin/"):
            by_label[label] = ref
    return list(by_label.values())


def first_parent(repo, sha):
    parents = run_git(repo, ["rev-list", "--parents", "-n", "1", sha]).split()
    return parents[1] if len(parents) > 1 else EMPTY_TREE


def web_base(repo):
    """Best-effort https base URL for the `origin` remote so reports can link
    to PRs/commits. e.g. git@github.com:org/repo.git -> https://github.com/org/repo.
    Returns None if the remote is missing or an unrecognised form."""
    url = run_git(repo, ["remote", "get-url", "origin"], check=False).strip()
    if not url:
        return None
    if url.startswith("git@"):
        host, _, path = url[4:].partition(":")
        if not path:
            return None
        base = "https://{}/{}".format(host, path)
    elif url.startswith("ssh://"):
        rest = url[len("ssh://"):].split("@")[-1]
        host, _, path = rest.partition("/")
        base = "https://{}/{}".format(host, path)
    elif url.startswith(("http://", "https://")):
        base = url.replace("http://", "https://", 1)
    else:
        return None
    if base.endswith(".git"):
        base = base[:-4]
    return base.rstrip("/")


def commit_body(repo, sha):
    """Full commit message body (no subject), trimmed."""
    return run_git(repo, ["log", "-1", "--format=%b", sha], check=False).strip()


def diff_numstat(repo, base, tip, paths):
    args = ["diff", "--numstat", base, tip]
    if paths:
        args += ["--"] + paths
    out = run_git(repo, args, check=False)
    files = insertions = deletions = 0
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) >= 3:
            files += 1
            insertions += int(cols[0]) if cols[0].isdigit() else 0
            deletions += int(cols[1]) if cols[1].isdigit() else 0
    return files, insertions, deletions


def compute_patch_ids(repo, emails):
    """Map sha -> patch_id using a single streamed `git log -p | git patch-id`."""
    log_proc = subprocess.Popen(
        ["git", "-C", str(repo), "log", "--all", "--no-merges"]
        + author_filter_args(emails)
        + ["-p"],
        stdout=subprocess.PIPE,
    )
    pid_proc = subprocess.Popen(
        ["git", "-C", str(repo), "patch-id", "--stable"],
        stdin=log_proc.stdout,
        stdout=subprocess.PIPE,
        text=True,
    )
    log_proc.stdout.close()  # allow log_proc to receive SIGPIPE if pid_proc exits
    out, _ = pid_proc.communicate()
    log_proc.wait()
    sha_to_patch = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            patch_id, sha = parts
            sha_to_patch[sha] = patch_id
    return sha_to_patch


def slugify(subject, max_len=60):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", subject.lower()).strip("-")
    return slug[:max_len].strip("-") or "work"


def month_of(iso_ts):
    return iso_ts[:7] if iso_ts else "unknown"


PR_RE = re.compile(r"\(#(\d+)\)")

# A squash-merge body that lists this many or more sub-PRs is a release /
# integration rollup (datafoundation -> master promotion), not one authored
# piece of work: you're the git author because you raised the release PR, but
# the diff is the whole team's work. Genuine feature PRs carry 0-1 sub-PRs.
ROLLUP_MIN_SUBPRS = 3
SUBPR_RE = re.compile(r"\(#\d+\)")
# Explicit release/promotion subjects that carry others' work even when the
# body doesn't enumerate sub-PRs (small hotfix/deployment promotions). Kept
# tight (needs a version number or the words hotfix/deployment) so genuine
# feature titles that merely contain "release" are not caught.
RELEASE_SUBJECT_RE = re.compile(
    r"(?i)(?:release\s+v?\d+\.\d|workplace data foundation\s+v?\d"
    r"|\bdeployment\b|\bhotfix\b)"
)


def pr_number(subject):
    match = PR_RE.search(subject or "")
    return match.group(1) if match else None


def rollup_reason(repo, sha, subject):
    """Return a reason string if this commit is a release/integration rollup
    (work that is not the author's own creative contribution), else None."""
    body = run_git(repo, ["show", "-s", "--format=%b", sha], check=False)
    n_subprs = len(SUBPR_RE.findall(body))
    if n_subprs >= ROLLUP_MIN_SUBPRS:
        return "aggregates {} sub-PRs".format(n_subprs)
    if RELEASE_SUBJECT_RE.search(subject or ""):
        return "release/promotion commit"
    return None


# Co-authors that don't imply another human's creative contribution.
COAUTHOR_BOT_HINTS = ("[bot]", "copilot", "ci-bot", "noreply.local", "actions@github")
COAUTHOR_RE = re.compile(r"(?im)^\s*Co-authored-by:\s*(.+?)\s*<(.+?)>\s*$")


def commit_coauthors(repo, sha, my_emails):
    """Real *other-human* co-authors on a commit (excludes you and bots)."""
    body = run_git(repo, ["show", "-s", "--format=%b", sha], check=False)
    mine = {e.lower() for e in my_emails}
    out = []
    for name, email in COAUTHOR_RE.findall(body):
        low = (name + " " + email).lower()
        if email.lower() in mine:
            continue
        if any(h in low for h in COAUTHOR_BOT_HINTS):
            continue
        label = "{} <{}>".format(name, email)
        if label not in out:
            out.append(label)
    return out


def per_file_numstat(repo, sha):
    """List of {path, ins, del} for a commit's own diff (against its parent)."""
    out = run_git(repo, ["show", "--numstat", "--format=", sha], check=False)
    files = []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) >= 3 and cols[2]:
            files.append({
                "path": cols[2],
                "ins": int(cols[0]) if cols[0].isdigit() else 0,
                "del": int(cols[1]) if cols[1].isdigit() else 0,
            })
    return files


# Subjects that signal a change excluded from "Works" by Appendix No. 1 §3
# (apparent-error fixes, renames, reverts, parameter/tag/id/config tweaks).
TRIVIAL_SUBJECT_RE = re.compile(
    r"(?i)("
    r"revert|re-?vert|"
    r"\bbump\b|version bump|"
    r"\brename[d]?\b|"
    r"\btypo\b|"
    r"reference the correct|to reference|"
    r"provide default value|default value for|"
    r"pause_status|"
    r"timeout|"
    r"change (git )?branch|"
    r"\btag\b|"
    r"job_id|"
    r"whitespace|reformat|formatting|\blint\b|"
    r"comment"
    r")")
CONFIG_EXTS = (".json", ".yml", ".yaml", ".cfg", ".ini", ".toml", ".txt")


def classify_creative(subject, insertions, deletions, paths):
    """Heuristic: is this likely a Work (creative) per the compliance rules?

    Returns (is_creative, reason). Advisory only -- the final call is yours.
    """
    total = (insertions or 0) + (deletions or 0)
    if TRIVIAL_SUBJECT_RE.search(subject or ""):
        return 0, "matches a non-Work pattern (App. 1 §3)"
    if total <= 3:
        return 0, "very small change ({} line{})".format(
            total, "" if total == 1 else "s")
    if paths and total <= 20 and all(
            p.lower().endswith(CONFIG_EXTS) for p in paths):
        return 0, "config/parameter-only change"
    return 1, ""


# ---------------------------------------------------------------------------
# AI-assisted descriptions (Copilot-in-VS-Code flow)
#
# Each creative ledger item can carry the structured, bilingual "Work"
# description the Xpertis report needs (Appendix No. 1 §1-5): what was created,
# what it relates to, your contribution, the creative element, and
# evidence/scope. Points 1-4 need a model to *read the diff*; point 5 is
# computed from the patch.
#
# Instead of shipping proprietary diffs to any external endpoint, the AI step
# runs INSIDE VS Code via GitHub Copilot -- the same boundary already approved
# for this repository, with nothing extra to install:
#   1. `describe --export` writes a small worklist of the patches still needing
#      a description (data/ai/<month>.worklist.json).
#   2. You run the saved `/xpertis-descriptions` prompt in Copilot Chat once;
#      Copilot reads each patch and writes data/ai/<month>.descriptions.json.
#   3. `describe --import <file>` folds those descriptions back into the ledger,
#      cached so re-runs are cheap.
# No description generated? The report still lists every creative item with a
# deterministic title and evidence/scope.
# ---------------------------------------------------------------------------

AI_DIR = DATA_DIR / "ai"
AI_TITLE_PREFIX_EN = "Creation of new source code for"
AI_TITLE_PREFIX_PL = "Utworzenie nowego kodu \u017ar\u00f3d\u0142owego dla"
AI_FIELDS = ("created", "relates_to", "contribution", "creative_element")


def _normalize_desc(entry):
    """Coerce one imported description into the stored shape, or None if it
    lacks the expected bilingual blocks."""
    if not isinstance(entry, dict) or "en" not in entry:
        return None
    result = {
        "model": entry.get("model", "copilot-vscode"),
        "generated_at": entry.get(
            "generated_at", datetime.now(timezone.utc).isoformat()),
    }
    for lang in ("en", "pl"):
        block = entry.get(lang) or {}
        result[lang] = {
            "topic": (block.get("topic") or "").strip(),
            "created": (block.get("created") or "").strip(),
            "relates_to": (block.get("relates_to") or "").strip(),
            "contribution": (block.get("contribution") or "").strip(),
            "creative_element": (block.get("creative_element") or "").strip(),
        }
    return result


def _export_worklist(conn, month=None, force=False):
    """Write per-month worklists of creative patches still needing a
    description, for the Copilot `/xpertis-descriptions` prompt to fill."""
    rows = _load_rows(conn, month=month)
    pending = [
        r for r in rows
        if r["is_creative"] and r["patch_path"]
        and (force or not (r["ai_json"] if "ai_json" in r.keys() else None))
    ]
    AI_DIR.mkdir(parents=True, exist_ok=True)
    # Worklists are ephemeral; clear old ones so a run never reprocesses a stale
    # month (descriptions files are kept -- they hold the actual output).
    for stale in AI_DIR.glob("*.worklist.json"):
        stale.unlink()
    if not pending:
        print("Nothing to export -- every creative item already has a "
              "description (use --force to redo).")
        return 0
    by_month = defaultdict(list)
    for r in pending:
        by_month[month_of(r["authored_at"])].append(r)
    for m, items in sorted(by_month.items()):
        desc_rel = "data/ai/{}.descriptions.json".format(m)
        worklist = {
            "month": m,
            "output_file": desc_rel,
            "instructions": (
                "For each item, open its `patch` file (a git format-patch: it "
                "contains the commit subject, message body and full diff). Write "
                "a factual, past-tense description grounded ONLY in that patch. "
                "Produce '{out}' as a JSON object keyed by each item's "
                "work_key, each value being {{\"en\": {{...}}, \"pl\": {{...}}}} "
                "with fields topic, created, relates_to, contribution, "
                "creative_element. 'topic' is a short noun phrase for the title "
                "'Creation of new source code for <topic>'. Fill English and "
                "Polish equivalently.".format(out=desc_rel)
            ),
            "items": [
                {
                    "work_key": r["work_key"],
                    "sha": (r["sha"] or "")[:10],
                    "subject": r["subject"] or "",
                    "patch": os.path.relpath(r["patch_path"], BASE_DIR),
                    "files_changed": r["files_changed"] or 0,
                    "insertions": r["insertions"] or 0,
                    "deletions": r["deletions"] or 0,
                }
                for r in items
            ],
        }
        out_path = AI_DIR / "{}.worklist.json".format(m)
        out_path.write_text(
            json.dumps(worklist, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Wrote {} ({} item(s) to describe).".format(
            out_path, len(items)))
    print("\nNext: run `/xpertis-descriptions` in Copilot Chat, then "
          "`describe --import`.")
    return 0


def _import_descriptions(conn, import_path):
    """Fold a Copilot-produced descriptions JSON back into the ledger."""
    path = Path(import_path)
    if not path.exists():
        print("Import file not found: {}".format(path), file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print("Could not read {}: {}".format(path, exc), file=sys.stderr)
        return 1
    # Accept {work_key: {...}} or {"descriptions"|"items": {...}|[...]}.
    if isinstance(data, dict) and ("descriptions" in data or "items" in data):
        data = data.get("descriptions") or data.get("items")
    entries = {}
    if isinstance(data, dict):
        entries = data
    elif isinstance(data, list):
        for e in data:
            if isinstance(e, dict) and e.get("work_key"):
                entries[e["work_key"]] = e
    if not entries:
        print("No descriptions found in {}.".format(path), file=sys.stderr)
        return 1
    known = {r[0] for r in conn.execute("SELECT work_key FROM authored_work")}
    updated = skipped = 0
    for work_key, entry in entries.items():
        norm = _normalize_desc(entry)
        if norm is None or work_key not in known:
            skipped += 1
            continue
        conn.execute(
            "UPDATE authored_work SET ai_json = ? WHERE work_key = ?",
            (json.dumps(norm, ensure_ascii=False), work_key))
        updated += 1
    conn.commit()
    print("Imported {} description(s); {} skipped.".format(updated, skipped))
    return 0


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS authored_work (
            work_key          TEXT PRIMARY KEY,
            pr_number         TEXT,
            patch_id          TEXT,
            sha               TEXT,
            subject           TEXT,
            body              TEXT,
            author_email      TEXT,
            authored_at       TEXT,
            files_changed     INTEGER,
            insertions        INTEGER,
            deletions         INTEGER,
            is_rollup         INTEGER DEFAULT 0,
            rollup_reason     TEXT,
            is_creative       INTEGER DEFAULT 1,
            creative_reason   TEXT,
            coauthors         TEXT,
            files_json        TEXT,
            patch_path        TEXT,
            first_seen_at     TEXT,
            last_synced_at    TEXT
        )
        """
    )
    # Add columns introduced after the original schema (safe on older DBs).
    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(authored_work)")}
    for col, ddl in (
        ("is_rollup", "is_rollup INTEGER DEFAULT 0"),
        ("rollup_reason", "rollup_reason TEXT"),
        ("is_creative", "is_creative INTEGER DEFAULT 1"),
        ("creative_reason", "creative_reason TEXT"),
        ("coauthors", "coauthors TEXT"),
        ("files_json", "files_json TEXT"),
        ("body", "body TEXT"),
        ("ai_json", "ai_json TEXT"),
    ):
        if col not in existing_cols:
            conn.execute("ALTER TABLE authored_work ADD COLUMN {}".format(ddl))
    conn.commit()


def _unlink_patch(path):
    """Remove a previously written patch file if it still exists under PATCHES_DIR."""
    if not path:
        return
    try:
        p = Path(path).resolve()
        if p.exists() and p.is_relative_to(PATCHES_DIR.resolve()):
            p.unlink()
    except OSError:
        pass


def prune_unreferenced_patches(conn):
    """Delete on-disk .patch files that no ledger row points at.

    Re-syncs used to leave orphan files behind whenever the representative SHA
    (and therefore the filename) for a work_key changed. Keep the folder in
    lockstep with the DB after every sync.
    """
    keep = {
        str(Path(r[0]).resolve())
        for r in conn.execute(
            "SELECT patch_path FROM authored_work "
            "WHERE patch_path IS NOT NULL AND patch_path != ''"
        )
        if r[0]
    }
    removed = 0
    if not PATCHES_DIR.exists():
        return removed
    for path in PATCHES_DIR.rglob("*.patch"):
        if str(path.resolve()) not in keep:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    # Drop empty month directories left behind by pruning.
    for month_dir in sorted(PATCHES_DIR.iterdir(), reverse=True):
        if month_dir.is_dir() and not any(month_dir.iterdir()):
            try:
                month_dir.rmdir()
            except OSError:
                pass
    return removed


def upsert_work(conn, row):
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT first_seen_at, patch_path FROM authored_work WHERE work_key = ?",
        (row["work_key"],),
    ).fetchone()
    first_seen = existing[0] if existing else now
    old_patch = existing[1] if existing else None
    conn.execute(
        """
        INSERT INTO authored_work (
            work_key, pr_number, patch_id, sha, subject, body, author_email,
            authored_at, files_changed, insertions, deletions,
            is_rollup, rollup_reason,
            is_creative, creative_reason, coauthors, files_json,
            patch_path, first_seen_at, last_synced_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(work_key) DO UPDATE SET
            pr_number=excluded.pr_number,
            patch_id=excluded.patch_id,
            sha=excluded.sha,
            subject=excluded.subject,
            body=excluded.body,
            author_email=excluded.author_email,
            authored_at=excluded.authored_at,
            files_changed=excluded.files_changed,
            insertions=excluded.insertions,
            deletions=excluded.deletions,
            is_rollup=excluded.is_rollup,
            rollup_reason=excluded.rollup_reason,
            is_creative=excluded.is_creative,
            creative_reason=excluded.creative_reason,
            coauthors=excluded.coauthors,
            files_json=excluded.files_json,
            patch_path=excluded.patch_path,
            last_synced_at=excluded.last_synced_at
        """,
        (
            row["work_key"], row["pr_number"], row["patch_id"], row["sha"],
            row["subject"], row.get("body"), row["author_email"], row["authored_at"],
            row["files_changed"], row["insertions"], row["deletions"],
            row.get("is_rollup", 0), row.get("rollup_reason"),
            row.get("is_creative", 1), row.get("creative_reason"),
            row.get("coauthors"), row.get("files_json"),
            row["patch_path"], first_seen, now,
        ),
    )
    conn.commit()
    new_patch = row.get("patch_path") or ""
    if old_patch and old_patch != new_patch:
        _unlink_patch(old_patch)
    return existing is None


def write_patch(repo, sha, authored_at, subject, work_key):
    """Write one patch file per work_key (stable name), not per SHA.

    Filenames used to embed sha[:10], so every time the representative commit
    for a work_key changed on re-sync, a sibling duplicate file appeared.
    """
    month_dir = PATCHES_DIR / month_of(authored_at).replace("-", "")
    month_dir.mkdir(parents=True, exist_ok=True)
    patch_path = month_dir / "{}-{}.patch".format(
        slugify(subject), slugify(work_key, max_len=40))
    content = run_git(repo, ["format-patch", "-1", sha, "--stdout"])
    patch_path.write_text(content, encoding="utf-8")
    return patch_path


def combined_diff(repo, base, tip, paths):
    diff_args = ["diff", base, tip]
    if paths:
        diff_args += ["--"] + paths
    return run_git(repo, diff_args, check=False)


def patch_id_of(repo, diff_text):
    proc = subprocess.run(
        ["git", "-C", str(repo), "patch-id", "--stable"],
        input=diff_text, stdout=subprocess.PIPE, text=True,
    )
    parts = proc.stdout.split()
    return parts[0] if parts else None


def write_combined_patch(work_key, base, tip, label, day, shas, commits, diff_text):
    month_dir = PATCHES_DIR / day.replace("-", "")[:6]
    month_dir.mkdir(parents=True, exist_ok=True)
    patch_path = month_dir / "{}-{}-{}.patch".format(
        slugify(label), day, slugify(work_key, max_len=40))
    header_lines = [
        "# Combined creative work",
        "# Branch: {}".format(label),
        "# Day:    {}".format(day),
        "# Commits ({}) in this bracket:".format(len(shas)),
    ]
    for s in shas:
        header_lines.append("#   {} {}".format(s[:10], commits[s]["subject"]))
    header_lines.append("# Diff range: {}..{}".format(base[:10], tip[:10]))
    header_lines.append("")
    patch_path.write_text("\n".join(header_lines) + "\n" + diff_text, encoding="utf-8")
    return patch_path


def build_work_rows(repo, commits, emails, sha_to_patch):
    """One row per piece of work: PR number, else patch-id (rebase-stable)."""
    # Step 1: collapse identical diffs by patch-id (atomic units -- never split).
    patch_groups = defaultdict(list)
    for sha in commits:
        pkey = sha_to_patch.get(sha) or ("nopatch:" + sha)
        patch_groups[pkey].append(sha)

    # Step 2: merge those units by PR number so every squash/rebase/WIP variant
    # of one PR becomes a single entry; units without a PR stay as-is.
    groups = defaultdict(list)
    for pkey, shas in patch_groups.items():
        pr = None
        for sha in shas:
            pr = pr or pr_number(commits[sha]["subject"])
        wkey = ("pr:" + pr) if pr else ("patch:" + pkey)
        groups[wkey].extend(shas)
    print("  {} distinct pieces of work after PR/patch-id dedup.".format(len(groups)))

    def pick_representative(shas):
        # Prefer the largest variant of the work (most complete diff).
        return max(
            shas,
            key=lambda s: (
                commits[s]["insertions"] + commits[s]["deletions"],
                commits[s]["authored_at"],
            ),
        )

    rows = []
    n_rollups = 0
    for key, shas in groups.items():
        rep = pick_representative(shas)
        meta = commits[rep]
        authored_at = min(commits[s]["authored_at"] for s in shas)
        reason = rollup_reason(repo, rep, meta["subject"])
        is_rollup = 1 if reason else 0
        n_rollups += is_rollup
        # Don't materialize patches for rollups -- they are the whole team's
        # work (often thousands of lines) and stay out of the ledger anyway.
        patch_path = "" if is_rollup else write_patch(
            repo, rep, authored_at, meta["subject"], key)
        coauthors = commit_coauthors(repo, rep, emails)
        files = per_file_numstat(repo, rep)
        is_creative, creative_reason = classify_creative(
            meta["subject"], meta["insertions"], meta["deletions"], meta["paths"])
        rows.append({
            "work_key": key,
            "pr_number": pr_number(meta["subject"]),
            "patch_id": sha_to_patch.get(rep),
            "sha": rep,
            "subject": meta["subject"],
            "body": None if is_rollup else commit_body(repo, rep),
            "author_email": emails[0],
            "authored_at": authored_at,
            "files_changed": meta["files"],
            "insertions": meta["insertions"],
            "deletions": meta["deletions"],
            "is_rollup": is_rollup,
            "rollup_reason": reason,
            "is_creative": is_creative,
            "creative_reason": creative_reason,
            "coauthors": "; ".join(coauthors) if coauthors else None,
            "files_json": json.dumps(files),
            "patch_path": str(patch_path),
        })
    print("  {} release/integration rollup(s) flagged and excluded.".format(
        n_rollups))
    return rows


def build_day_rows(repo, commits, emails, sha_to_patch):
    """One row per (feature branch, day): my commits that day combined into a
    single diff -- so several edits to the same objects become one creative work.

    Each feature branch is walked in topological (ancestry) order, and commits
    are globally de-duplicated by patch-id so rebase copies (within a branch and
    across sibling branches) collapse to a single piece of work.
    """
    rows = []
    seen_patch = set()
    seen_bracket = set()
    branch_count = 0
    for ref in dev_branch_refs(repo):
        label = normalize_label(ref)
        listed = run_git(
            repo,
            ["rev-list", "--topo-order", "--reverse", ref] + author_filter_args(emails),
            check=False,
        ).split()
        branch_shas = []
        for sha in listed:
            if sha not in commits:
                continue
            key = sha_to_patch.get(sha) or ("nopatch:" + sha)
            if key in seen_patch:
                continue
            seen_patch.add(key)
            branch_shas.append(sha)
        if not branch_shas:
            continue
        branch_count += 1

        # One bracket per day; branch_shas is oldest->newest (topological).
        day_to_shas = defaultdict(list)
        for sha in branch_shas:
            day_to_shas[commits[sha]["authored_at"][:10]].append(sha)

        for day, gshas in day_to_shas.items():
            first, tip = gshas[0], gshas[-1]
            base = first_parent(repo, first)
            paths = sorted({p for s in gshas for p in commits[s]["paths"]})
            diff_text = combined_diff(repo, base, tip, paths)

            # Drop brackets whose net diff is identical to one already recorded
            # (e.g. the same feature continued on a sibling dev branch).
            bracket_id = patch_id_of(repo, diff_text)
            if bracket_id and bracket_id in seen_bracket:
                continue
            if bracket_id:
                seen_bracket.add(bracket_id)

            files, insertions, deletions = diff_numstat(repo, base, tip, paths)
            n = len(gshas)
            subject = "{} — {} ({} commit{})".format(
                label, day, n, "s" if n != 1 else "")
            work_key = "day:{}:{}".format(label, day)
            patch_path = write_combined_patch(
                work_key, base, tip, label, day, gshas, commits, diff_text)
            rows.append({
                "work_key": work_key,
                "pr_number": None,
                "patch_id": None,
                "sha": tip,
                "subject": subject,
                "body": None,
                "author_email": emails[0],
                "authored_at": commits[first]["authored_at"],
                "files_changed": files,
                "insertions": insertions,
                "deletions": deletions,
                "is_rollup": 0,
                "rollup_reason": None,
                "is_creative": classify_creative(subject, insertions, deletions,
                                                 paths)[0],
                "creative_reason": classify_creative(subject, insertions,
                                                     deletions, paths)[1],
                "coauthors": None,
                "files_json": json.dumps([
                    {"path": p, "ins": 0, "del": 0} for p in paths]),
                "patch_path": str(patch_path),
            })
    print("  {} feature-branch day brackets across {} branch(es).".format(
        len(rows), branch_count))
    return rows


def cmd_sync(args):
    config = load_config()
    repo, emails = config["repo"], config["emails"]
    DATA_DIR.mkdir(exist_ok=True)
    PATCHES_DIR.mkdir(exist_ok=True)

    if not args.no_fetch:
        fetch(repo)

    print("Scanning commits authored by: {}".format(", ".join(emails)))
    commits = collect_commits(repo, emails)
    print("  {} raw commits authored by you.".format(len(commits)))

    commits = filter_commits_by_date(commits, args.since, args.until, args.month)
    if args.since or args.until or args.month:
        print("  {} after date filter.".format(len(commits)))
    if not commits:
        print("Nothing to do.")
        return 0

    sha_to_patch = compute_patch_ids(repo, emails)

    if args.mode == "day":
        rows = build_day_rows(repo, commits, emails, sha_to_patch)
    else:
        rows = build_work_rows(repo, commits, emails, sha_to_patch)

    conn = sqlite3.connect(str(DB_PATH))
    new_count = 0
    pruned = 0
    try:
        init_db(conn)
        if args.reset:
            conn.execute("DELETE FROM authored_work")
            conn.commit()
        for row in rows:
            new_count += 1 if upsert_work(conn, row) else 0
        pruned = prune_unreferenced_patches(conn)
    finally:
        conn.close()

    kept = [r for r in rows if not r.get("is_rollup")]
    to_review = sum(1 for r in kept if not r.get("is_creative", 1))
    coauthored = sum(1 for r in kept if r.get("coauthors"))
    print("Done ({} mode). {} new, {} total in this run.".format(
        args.mode, new_count, len(rows)))
    if pruned:
        print("  pruned {} orphan patch file(s).".format(pruned))
    print("  {} ledger item(s): {} flagged 'review' (likely not a Work), "
          "{} co-authored.".format(len(kept), to_review, coauthored))
    return 0


def cmd_describe(args):
    """Export creative patches needing a bilingual 'Work' description, or
    import the descriptions Copilot produced. Cached in the DB, so re-runs are
    cheap and only new items need Copilot (unless --force)."""
    if not DB_PATH.exists():
        print("No database yet -- run `sync` first.", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        init_db(conn)
        if args.import_file:
            return _import_descriptions(conn, args.import_file)
        # Default to the current month only; --all backfills every pending month.
        month = args.month
        if not month and not getattr(args, "all", False):
            month = date.today().strftime("%Y-%m")
        return _export_worklist(conn, month=month, force=args.force)
    finally:
        conn.close()


def _rolling_since():
    """First day of the previous month -- covers the month you're about to
    report plus the current one (matches run_sync.sh)."""
    first_this = date.today().replace(day=1)
    last_prev = first_this - timedelta(days=1)
    return last_prev.replace(day=1).isoformat()


def cmd_monthly(args):
    """One-button monthly run: sync new work, build the report, and export the
    worklist of items still needing a Copilot description. Scoped to a single
    month -- the current one by default, or --month YYYY-MM."""
    target_month = args.month or date.today().strftime("%Y-%m")
    # Sync a rolling window (covers late commits from the prior month too), but
    # scope the report and worklist to the target month only.
    since = None if args.month else _rolling_since()
    sync_ns = argparse.Namespace(
        no_fetch=args.no_fetch, mode="work", since=since, until=None,
        month=args.month, reset=False)
    print("== 1/3 sync ==")
    cmd_sync(sync_ns)
    print("\n== 2/3 report ({}) ==".format(target_month))
    cmd_report(argparse.Namespace(month=target_month, html=True))
    print("\n== 3/3 export worklist ({}) ==".format(target_month))
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        init_db(conn)
        _export_worklist(conn, month=target_month, force=False)
    finally:
        conn.close()
    print("\nDeterministic report is ready. To fill the AI descriptions, run "
          "`/xpertis-descriptions` in Copilot Chat.")
    return 0


def _fmt_date(iso_ts):
    if not iso_ts:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_ts)
        return dt.strftime("%Y-%m-%d %H:%M %z").strip()
    except ValueError:
        return iso_ts


def _load_rows(conn, month=None, rollups=False):
    sql = "SELECT * FROM authored_work"
    clauses = []
    params = []
    if month:
        clauses.append("substr(authored_at, 1, 7) = ?")
        params.append(month)
    # By default hide release/integration rollups; `rollups=True` shows only them.
    clauses.append("COALESCE(is_rollup, 0) = {}".format(1 if rollups else 0))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY authored_at DESC"
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, params).fetchall()


def cmd_report(args):
    if not DB_PATH.exists():
        print("No database yet -- run `sync` first.", file=sys.stderr)
        return 1
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = _load_rows(conn, month=args.month)
    finally:
        conn.close()
    if not rows:
        print("No work found for the requested range.")
        return 0

    by_month = defaultdict(list)
    for row in rows:
        by_month[month_of(row["authored_at"])].append(row)

    web = None
    try:
        web = web_base(load_config()["repo"])
    except (ConfigError, GitError):
        pass

    for month in sorted(by_month, reverse=True):
        month_rows = by_month[month]
        out_path = REPORTS_DIR / "{}.md".format(month)
        out_path.write_text(_render_month(month, month_rows, web), encoding="utf-8")
        msg = "Wrote {} ({} items)".format(out_path, len(month_rows))
        if getattr(args, "html", False):
            html_path = REPORTS_DIR / "{}.html".format(month)
            html_path.write_text(
                _render_month_html(month, month_rows, web), encoding="utf-8")
            msg += " + {}".format(html_path.name)
        print(msg)

    _write_excluded_report(month=args.month)
    return 0


def _short_date(iso_ts):
    return (iso_ts or "")[:10] or "?"


def _pr_url(web, pr):
    return "{}/pull/{}".format(web, pr) if web and pr else None


def _commit_url(web, sha):
    return "{}/commit/{}".format(web, sha) if web and sha else None


def _creative_label(row):
    if row["is_creative"]:
        return "creative"
    return "review — {}".format(row["creative_reason"] or "likely not a Work")


def _files_of(row):
    try:
        return json.loads(row["files_json"]) if row["files_json"] else []
    except (ValueError, TypeError):
        return []


def _ai_of(row):
    """Parsed ai_json for a row, or None."""
    raw = row["ai_json"] if "ai_json" in row.keys() else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _fallback_topic(row):
    """Deterministic title topic when no AI description exists: the commit
    subject with the trailing `(#123)` PR marker stripped."""
    subj = (row["subject"] or "").strip()
    subj = PR_RE.sub("", subj).strip(" -")
    return subj or "work"


def _patch_basename(row):
    return os.path.basename(row["patch_path"]) if row["patch_path"] else ""


def _creative_items(month_rows):
    """Creative, patch-backed items sorted alphabetically by patch filename --
    the 'alphabetical list of patch files of creative work items'."""
    items = [r for r in month_rows if r["is_creative"] and r["patch_path"]]
    return sorted(items, key=lambda r: _patch_basename(r).lower())


def _creative_anchor(row):
    """Stable anchor id for a creative item's description entry."""
    return "cw-" + slugify(row["work_key"], max_len=40)


def _row_title(row, max_len=70):
    """Human title for the summary table: the Copilot topic if present, else the
    commit subject (PR marker stripped). Safe for a Markdown table cell."""
    ai = _ai_of(row)
    title = (ai["en"]["topic"] if ai else "") or _fallback_topic(row)
    title = title.replace("|", "\\|").replace("\n", " ").strip()
    if len(title) > max_len:
        title = title[:max_len - 1].rstrip() + "…"
    return title or "—"


# Labels for the five Appendix No. 1 §1-5 description points, per language.
_DESC_LABELS = {
    "en": ("What was created", "What it relates to", "Your contribution",
           "Creative element", "Evidence and scope"),
    "pl": ("Co zosta\u0142o utworzone", "Czego dotyczy", "Tw\u00f3j wk\u0142ad",
           "Element tw\u00f3rczy", "Dow\u00f3d i zakres"),
}


def _evidence_text(row, lang):
    """Deterministic point 5: the file an Approver can open + scope figures."""
    name = _patch_basename(row) or "(patch)"
    n = row["files_changed"] or 0
    ins = row["insertions"] or 0
    dele = row["deletions"] or 0
    if lang == "pl":
        return "Plik {} \u2014 {} plik(\u00f3w), +{}/\u2212{} linii.".format(
            name, n, ins, dele)
    return "File {} \u2014 {} file(s), +{}/\u2212{} lines.".format(
        name, n, ins, dele)


def _render_creative_section(month_rows, web=None):
    """Markdown: alphabetical creative-work items with bilingual titles and the
    structured five-point description (AI-filled where available)."""
    items = _creative_items(month_rows)
    lines = ["## Creative work items (alphabetical) — Xpertis descriptions", ""]
    if not items:
        lines.append("_No creative items with patches for this month._")
        lines.append("")
        return lines
    lines.append(
        "> Titles follow *{} **&lt;topic&gt;***; each item carries the five-point "
        "description (Appendix No. 1 §1-5) in English and Polish. Points 1-4 are "
        "AI-drafted from the diff and **must be reviewed**; point 5 (evidence & "
        "scope) is computed from the patch.".format(AI_TITLE_PREFIX_EN))
    lines.append("")
    any_missing = False
    for r in items:
        ai = _ai_of(r)
        topic_en = (ai["en"]["topic"] if ai else "") or _fallback_topic(r)
        topic_pl = (ai["pl"]["topic"] if ai else "") or _fallback_topic(r)
        lines.append('<a id="{}"></a>'.format(_creative_anchor(r)))
        lines.append("### {} **{}**".format(AI_TITLE_PREFIX_EN, topic_en))
        lines.append("")
        lines.append("*{} **{}***".format(AI_TITLE_PREFIX_PL, topic_pl))
        lines.append("")
        if not ai:
            any_missing = True
            lines.append(
                "> _AI description pending — run `describe` to generate it._")
            lines.append("")
        for lang in ("en", "pl"):
            labels = _DESC_LABELS[lang]
            block = ai[lang] if ai else {}
            lines.append("**{}**".format("English" if lang == "en" else "Polski"))
            lines.append("")
            for idx, field in enumerate(AI_FIELDS):
                val = (block.get(field) or "—") if ai else "—"
                lines.append("{}. **{}:** {}".format(idx + 1, labels[idx], val))
            lines.append("5. **{}:** {}".format(
                labels[4], _evidence_text(r, lang)))
            lines.append("")
        rel = os.path.relpath(r["patch_path"], REPORTS_DIR)
        lines.append("[Open patch: {}]({})".format(_patch_basename(r), rel))
        lines.append("")
        lines.append("---")
        lines.append("")
    if any_missing:
        lines.append(
            "_Tip: run_ `python commit_tracker.py describe` _to fill pending "
            "descriptions, or_ `python commit_tracker.py monthly` _to do "
            "everything at once._")
        lines.append("")
    return lines


def _render_month(month, month_rows, web=None):
    lines = ["# My authored work -- {}".format(month), ""]
    lines.extend([
        "> **Candidate list — not an official submission.** Auto-generated from",
        "> git history to help prepare your report in the Company's System",
        "> (Xpertis). Before reporting during the Registration Period (1st-14th of",
        "> the following month), review each item against Appendix No. 1, pick the",
        "> files that are your Work, and drop non-Works (minor/parameter/config",
        "> changes, apparent-error fixes, reverts, comments, auto-generated output).",
        "> Rows marked **review** are likely not Works; **co-authored** rows mix in",
        "> a teammate's work — report only your own files/contribution.",
        "",
    ])
    total_add = sum(r["insertions"] or 0 for r in month_rows)
    total_del = sum(r["deletions"] or 0 for r in month_rows)
    creative = sum(1 for r in month_rows if r["is_creative"])
    coauth = sum(1 for r in month_rows if r["coauthors"])
    lines.append(
        "**{} item(s)** — {} likely creative, {} to review, {} co-authored. "
        "+{} / -{} lines total.".format(
            len(month_rows), creative, len(month_rows) - creative, coauth,
            total_add, total_del))
    lines.append("")

    # Scannable summary table -- everything needed to triage without opening patches.
    lines.append(
        "| Date | Commit | PR | Title (→ description) | Files | +/- | "
        "Assessment | Co-authored |")
    lines.append(
        "|------|--------|----|----------------------|-------|-----|"
        "------------|-------------|")
    described = {r["work_key"] for r in _creative_items(month_rows)}
    for i, r in enumerate(month_rows, 1):
        if r["pr_number"]:
            purl = _pr_url(web, r["pr_number"])
            pr = "[#{}]({})".format(r["pr_number"], purl) if purl \
                else "#{}".format(r["pr_number"])
        else:
            pr = "—"
        # Link the title to its Copilot description when the item has one.
        title = _row_title(r)
        if r["work_key"] in described:
            title = "[{}](#{})".format(title, _creative_anchor(r))
        co = r["coauthors"] or "—"
        lines.append(
            "| {} | [{}](#{}) | {} | {} | {} | +{}/-{} | {} | {} |".format(
                _short_date(r["authored_at"]), (r["sha"] or "")[:10], i, pr,
                title, r["files_changed"] or 0, r["insertions"] or 0,
                r["deletions"] or 0, _creative_label(r), co))
    lines.append("")
    lines.append("---")
    lines.append("")

    # Alphabetical creative-work items with the bilingual Xpertis descriptions.
    lines.extend(_render_creative_section(month_rows, web))
    lines.append("---")
    lines.append("")

    # Per-item detail with per-file line counts, so you can choose files to report.
    for i, r in enumerate(month_rows, 1):
        lines.append('<a id="{}"></a>'.format(i))
        lines.append("## {}. {}".format(i, r["subject"] or "(no subject)"))
        lines.append("")
        sha10 = (r["sha"] or "")[:10]
        curl = _commit_url(web, r["sha"])
        commit_txt = "[`{}`]({})".format(sha10, curl) if curl else "`{}`".format(sha10)
        line = "- Date: {} · Commit {}".format(_fmt_date(r["authored_at"]), commit_txt)
        if r["pr_number"]:
            purl = _pr_url(web, r["pr_number"])
            line += " · PR " + ("[#{}]({})".format(r["pr_number"], purl) if purl
                                else "#{}".format(r["pr_number"]))
        lines.append(line)
        body = (r["body"] or "").strip() if "body" in r.keys() else ""
        if body:
            lines.append("")
            for bl in body.splitlines():
                lines.append("> {}".format(bl) if bl.strip() else ">")
            lines.append("")
        lines.append("- Total: +{} / -{} across {} file(s)".format(
            r["insertions"] or 0, r["deletions"] or 0, r["files_changed"] or 0))
        lines.append("- Assessment: {}".format(
            "likely creative work" if r["is_creative"]
            else "likely NOT a Work — {}".format(r["creative_reason"] or "review")))
        if r["coauthors"]:
            lines.append("- Co-authored with **{}** — report only your own "
                         "files/contribution.".format(r["coauthors"]))
        files = _files_of(r)
        if files:
            lines.append("- Files (+/-):")
            for f in files:
                lines.append("    - `{}` — +{}/-{}".format(
                    f.get("path", "?"), f.get("ins", 0), f.get("del", 0)))
        if r["patch_path"]:
            rel = os.path.relpath(r["patch_path"], REPORTS_DIR)
            lines.append("- Patch: [{}]({})".format(
                os.path.basename(r["patch_path"]), rel))
        lines.append("")
    return "\n".join(lines)


REPORT_CSS = """
:root { --fg:#1f2328; --muted:#656d76; --line:#d0d7de; --accent:#0969da;
        --warn-bg:#fff8c5; --warn-line:#d4a72c; --chip:#eaeef2; }
* { box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,
       sans-serif; color:var(--fg); max-width:960px; margin:32px auto; padding:0 20px;
       line-height:1.5; }
h1 { font-size:24px; margin:0 0 4px; }
h2 { font-size:17px; margin:28px 0 8px; border-top:1px solid var(--line);
     padding-top:16px; }
.note { background:var(--warn-bg); border:1px solid var(--warn-line);
        border-radius:6px; padding:12px 14px; font-size:13px; color:#4d3800;
        margin:16px 0; }
.summary { font-size:14px; margin:12px 0 20px; }
table { border-collapse:collapse; width:100%; font-size:13px; margin:12px 0; }
th,td { border:1px solid var(--line); padding:6px 8px; text-align:left;
        vertical-align:top; }
th { background:var(--chip); }
code { background:var(--chip); padding:1px 5px; border-radius:5px;
       font-size:12px; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.chip { display:inline-block; font-size:11px; padding:1px 7px; border-radius:10px; }
.creative { background:#dafbe1; color:#1a7f37; }
.review { background:#ffebe9; color:#cf222e; }
.meta { color:var(--muted); font-size:13px; margin:2px 0 8px; }
blockquote { margin:8px 0; padding:6px 12px; border-left:3px solid var(--line);
             color:var(--muted); white-space:pre-wrap; font-size:13px; }
ul.files { margin:6px 0; padding-left:20px; font-size:13px; }
.co { color:#9a6700; }
h2.section { color:var(--accent); }
.work { border:1px solid var(--line); border-radius:6px; padding:4px 16px 12px;
        margin:16px 0; }
.work h3 { font-size:15px; margin:12px 0 2px; }
.work ol { margin:4px 0 12px; padding-left:22px; font-size:13px; }
@media print { body { margin:0; max-width:none; } a { color:var(--fg); } }
"""


def _esc(text):
    return html.escape(str(text if text is not None else ""))


def _render_creative_section_html(month_rows):
    """HTML counterpart of _render_creative_section."""
    items = _creative_items(month_rows)
    p = ['<h2 class="section">Creative work items (alphabetical) — Xpertis '
         "descriptions</h2>"]
    if not items:
        p.append("<p><em>No creative items with patches for this month.</em></p>")
        return "\n".join(p)
    p.append(
        '<p class="summary">Titles follow <em>{} &lt;topic&gt;</em>; each item '
        "carries the five-point description (Appendix No. 1 §1-5) in English and "
        "Polish. Points 1-4 are AI-drafted from the diff and <strong>must be "
        "reviewed</strong>; point 5 is computed from the patch.</p>".format(
            _esc(AI_TITLE_PREFIX_EN)))
    for r in items:
        ai = _ai_of(r)
        topic_en = _esc((ai["en"]["topic"] if ai else "") or _fallback_topic(r))
        topic_pl = _esc((ai["pl"]["topic"] if ai else "") or _fallback_topic(r))
        p.append('<div class="work" id="{}">'.format(_esc(_creative_anchor(r))))
        p.append("<h3>{} <strong>{}</strong></h3>".format(
            _esc(AI_TITLE_PREFIX_EN), topic_en))
        p.append('<p class="meta"><em>{} <strong>{}</strong></em></p>'.format(
            _esc(AI_TITLE_PREFIX_PL), topic_pl))
        if not ai:
            p.append('<p class="note">AI description pending — run the '
                     "<code>/xpertis-descriptions</code> Copilot prompt.</p>")
        for lang in ("en", "pl"):
            labels = _DESC_LABELS[lang]
            block = ai[lang] if ai else {}
            p.append("<p><strong>{}</strong></p><ol>".format(
                "English" if lang == "en" else "Polski"))
            for idx, field in enumerate(AI_FIELDS):
                val = _esc((block.get(field) or "—") if ai else "—")
                p.append("<li><strong>{}:</strong> {}</li>".format(
                    _esc(labels[idx]), val))
            p.append("<li><strong>{}:</strong> {}</li>".format(
                _esc(labels[4]), _esc(_evidence_text(r, lang))))
            p.append("</ol>")
        rel = os.path.relpath(r["patch_path"], REPORTS_DIR)
        p.append('<p><a href="{}">Open patch: {}</a></p>'.format(
            _esc(rel), _esc(_patch_basename(r))))
        p.append("</div>")
    return "\n".join(p)


def _render_month_html(month, month_rows, web=None):
    creative = sum(1 for r in month_rows if r["is_creative"])
    coauth = sum(1 for r in month_rows if r["coauthors"])
    total_add = sum(r["insertions"] or 0 for r in month_rows)
    total_del = sum(r["deletions"] or 0 for r in month_rows)
    p = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         "<title>My authored work — {}</title>".format(_esc(month)),
         "<style>{}</style></head><body>".format(REPORT_CSS)]
    p.append("<h1>My authored work — {}</h1>".format(_esc(month)))
    p.append(
        '<div class="note"><strong>Candidate list — not an official submission.</strong> '
        "Auto-generated from git history to help prepare your report in the Company's "
        "System (Xpertis). Before reporting during the Registration Period (1st–14th of "
        "the following month), review each item against Appendix No. 1, pick the files "
        "that are your Work, and drop non-Works (minor/parameter/config changes, "
        "apparent-error fixes, reverts, comments, auto-generated output). Rows marked "
        "<strong>review</strong> are likely not Works; <strong>co-authored</strong> rows "
        "mix in a teammate's work — report only your own files/contribution.</div>")
    p.append(
        '<p class="summary"><strong>{} item(s)</strong> — {} likely creative, {} to '
        "review, {} co-authored. +{} / −{} lines total.</p>".format(
            len(month_rows), creative, len(month_rows) - creative, coauth,
            total_add, total_del))

    p.append("<table><thead><tr><th>#</th><th>Date</th><th>Commit</th><th>PR</th>"
             "<th>Title (→ description)</th>"
             "<th>Files</th><th>+/−</th><th>Assessment</th><th>Co-authored</th>"
             "</tr></thead><tbody>")
    described = {r["work_key"] for r in _creative_items(month_rows)}
    for i, r in enumerate(month_rows, 1):
        sha10 = (r["sha"] or "")[:10]
        curl = _commit_url(web, r["sha"])
        commit = ('<a href="{}"><code>{}</code></a>'.format(_esc(curl), _esc(sha10))
                  if curl else "<code>{}</code>".format(_esc(sha10)))
        if r["pr_number"]:
            purl = _pr_url(web, r["pr_number"])
            pr = ('<a href="{}">#{}</a>'.format(_esc(purl), _esc(r["pr_number"]))
                  if purl else "#{}".format(_esc(r["pr_number"])))
        else:
            pr = "—"
        title = _esc(_row_title(r))
        if r["work_key"] in described:
            title = '<a href="#{}">{}</a>'.format(
                _esc(_creative_anchor(r)), title)
        cls, txt = ("creative", "creative") if r["is_creative"] else (
            "review", "review")
        assess = '<span class="chip {}">{}</span>'.format(cls, txt)
        co = _esc(r["coauthors"]) if r["coauthors"] else "—"
        p.append(
            '<tr><td>{}</td><td>{}</td><td><a href="#i{}">{}</a></td><td>{}</td>'
            "<td>{}</td><td>{}</td><td>+{}/−{}</td><td>{}</td><td>{}</td></tr>".format(
                i, _esc(_short_date(r["authored_at"])), i, commit, pr, title,
                r["files_changed"] or 0, r["insertions"] or 0,
                r["deletions"] or 0, assess, co))
    p.append("</tbody></table>")

    # Alphabetical creative-work items with the bilingual Xpertis descriptions.
    p.append(_render_creative_section_html(month_rows))

    for i, r in enumerate(month_rows, 1):
        p.append('<h2 id="i{}">{}. {}</h2>'.format(
            i, i, _esc(r["subject"] or "(no subject)")))
        sha10 = (r["sha"] or "")[:10]
        curl = _commit_url(web, r["sha"])
        commit = ('<a href="{}"><code>{}</code></a>'.format(_esc(curl), _esc(sha10))
                  if curl else "<code>{}</code>".format(_esc(sha10)))
        meta = "{} · Commit {}".format(_esc(_fmt_date(r["authored_at"])), commit)
        if r["pr_number"]:
            purl = _pr_url(web, r["pr_number"])
            meta += " · PR " + (
                '<a href="{}">#{}</a>'.format(_esc(purl), _esc(r["pr_number"]))
                if purl else "#{}".format(_esc(r["pr_number"])))
        p.append('<p class="meta">{}</p>'.format(meta))
        body = (r["body"] or "").strip() if "body" in r.keys() else ""
        if body:
            p.append("<blockquote>{}</blockquote>".format(_esc(body)))
        assess = ("likely creative work" if r["is_creative"]
                  else "likely NOT a Work — {}".format(r["creative_reason"] or "review"))
        p.append("<p><strong>Total:</strong> +{} / −{} across {} file(s)<br>"
                 "<strong>Assessment:</strong> {}</p>".format(
                     r["insertions"] or 0, r["deletions"] or 0,
                     r["files_changed"] or 0, _esc(assess)))
        if r["coauthors"]:
            p.append('<p class="co"><strong>Co-authored with {}</strong> — report '
                     "only your own files/contribution.</p>".format(
                         _esc(r["coauthors"])))
        files = _files_of(r)
        if files:
            p.append('<ul class="files">')
            for f in files:
                p.append("<li><code>{}</code> — +{}/−{}</li>".format(
                    _esc(f.get("path", "?")), f.get("ins", 0), f.get("del", 0)))
            p.append("</ul>")
        if r["patch_path"]:
            rel = os.path.relpath(r["patch_path"], REPORTS_DIR)
            p.append('<p><a href="{}">{}</a></p>'.format(
                _esc(rel), _esc(os.path.basename(r["patch_path"]))))
    p.append("</body></html>")
    return "\n".join(p)


def _write_excluded_report(month=None):
    """List rollups excluded from the ledger, for transparency/audit."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = _load_rows(conn, month=month, rollups=True)
    finally:
        conn.close()
    out_path = REPORTS_DIR / "excluded.md"
    if not rows:
        if out_path.exists():
            out_path.unlink()
        return
    lines = [
        "# Excluded from the ledger — release / integration rollups",
        "",
        "> These commits are authored by you in git only because you raised the",
        "> release/promotion PR. Their diff is the whole team's squashed work, not",
        "> your individual creative contribution, so they are **excluded** from your",
        "> reported Works. Listed here for transparency; review if you disagree.",
        "",
        "**{} excluded item(s).**".format(len(rows)),
        "",
    ]
    for r in rows:
        lines.append("## {}".format(r["subject"] or "(no subject)"))
        lines.append("")
        lines.append("- Authored: {}".format(_fmt_date(r["authored_at"])))
        lines.append("- Reason: {}".format(r["rollup_reason"] or "rollup"))
        lines.append("- Size: +{} / -{} across {} file(s)".format(
            r["insertions"] or 0, r["deletions"] or 0, r["files_changed"] or 0))
        lines.append("- Commit: `{}`".format((r["sha"] or "")[:10]))
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("Wrote {} ({} excluded rollups)".format(out_path, len(rows)))


def cmd_list(args):
    if not DB_PATH.exists():
        print("No database yet -- run `sync` first.", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = _load_rows(conn, month=args.month, rollups=args.rollups)
    finally:
        conn.close()
    for r in rows:
        # ~ = likely not a Work (review), & = co-authored.
        creative = " " if r["is_creative"] else "~"
        co = "&" if r["coauthors"] else " "
        print("[{}{}] {}  {:<10}  {}".format(
            creative, co, month_of(r["authored_at"]),
            (r["sha"] or "")[:10], r["subject"] or ""))
    label = "excluded rollup(s)" if args.rollups else "item(s)"
    legend = "" if args.rollups else "  (~=review  &=co-authored)"
    print("{} {}.{}".format(len(rows), label, legend))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Fetch + scan authored commits into the store.")
    p_sync.add_argument("--no-fetch", action="store_true",
                        help="Skip `git fetch`; scan the clone as-is.")
    p_sync.add_argument("--mode", choices=["work", "day"], default="work",
                        help="work = one entry per PR/patch (default); "
                             "day = one combined diff per feature branch per day.")
    p_sync.add_argument("--since", help="Only commits authored on/after YYYY-MM-DD.")
    p_sync.add_argument("--until", help="Only commits authored on/before YYYY-MM-DD.")
    p_sync.add_argument("--month", help="Only commits authored in YYYY-MM.")
    p_sync.add_argument("--reset", action="store_true",
                        help="Clear the table first (use when switching --mode).")
    p_sync.set_defaults(func=cmd_sync)

    p_report = sub.add_parser("report", help="Generate monthly Markdown reports.")
    p_report.add_argument("--month", help="Limit to a single month, e.g. 2026-06.")
    p_report.add_argument("--html", action="store_true",
                          help="Also write a styled, self-contained .html per month.")
    p_report.set_defaults(func=cmd_report)

    p_desc = sub.add_parser(
        "describe",
        help="Export creative patches for Copilot to describe, or import the "
             "descriptions it produced.")
    p_desc.add_argument("--month", help="Limit to a single month, e.g. 2026-06.")
    p_desc.add_argument("--all", action="store_true",
                        help="Export every pending month (backfill), not just "
                             "the current one.")
    p_desc.add_argument("--import", dest="import_file", metavar="FILE",
                        help="Import a Copilot-produced descriptions JSON.")
    p_desc.add_argument("--force", action="store_true",
                        help="Re-export items that already have a description.")
    p_desc.set_defaults(func=cmd_describe)

    p_monthly = sub.add_parser(
        "monthly",
        help="One button: sync + report + export the Copilot worklist.")
    p_monthly.add_argument("--month", help="Target a specific month, e.g. 2026-06 "
                           "(default: the current month only).")
    p_monthly.add_argument("--no-fetch", action="store_true",
                           help="Skip `git fetch`; scan the clone as-is.")
    p_monthly.set_defaults(func=cmd_monthly)

    p_list = sub.add_parser("list", help="Print a quick listing to the console.")
    p_list.add_argument("--month", help="Limit to a single month, e.g. 2026-06.")
    p_list.add_argument("--rollups", action="store_true",
                        help="Show only the excluded release/integration rollups.")
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, GitError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
