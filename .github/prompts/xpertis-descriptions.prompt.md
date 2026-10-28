---
mode: agent
description: One-button monthly run — sync commits, then use Copilot to write the bilingual Xpertis "Work" descriptions for each creative patch and fold them back into the report.
---

# Generate the monthly Xpertis creative-work descriptions

You are completing the AI step of the `commit_tracker` tool. Everything happens
**locally in this workspace** — do not send patch contents to any external
service or fetch any URL. Work only from files on disk.

Use `.venv/bin/python` if it exists, otherwise `python3`.

## What Xpertis requires (from September 2026)

Each reported **Work** must include these five elements. Reports that omit them
are returned; if not completed in time they are rejected. Describe **every**
creative Work from the month, **each Work separately** — not a monthly summary
and not one entry for a whole project.

1. **What was created** — a short explanation of the **problem it solves**
   (the result name is already the attachment / title — do not repeat it).
2. **What it relates to** — the specific program, application or component
   concerned, not only the project name.
3. **Your contribution** — what you personally did. For team work, only your
   part (module, model, or functionality in this diff).
4. **Creative element** — which part comes from your own concept, not a
   ready-made pattern, standard, or routine step. If AI tools were used, name
   the human intellectual contribution *beyond* the generated output. AI output
   without that contribution is not a Work.
5. **Evidence and scope** — a file the Approver can open, plus scope (files and
   lines). **You do not write this** — the tool fills it from the patch.

A link or list of files is supporting evidence only; it does not replace 1–4.
SECRET/confidential work still needs 1–4 in enough detail to verify creativity,
without leaking sensitive content.

## How the five elements are split across Xpertis fields

Xpertis gives three carriers per Work, and the tool fills all three from your
four fields:

| Carrier | What goes there | Who writes it |
|---|---|---|
| Attachment — the `.patch` file itself | Full numbered 1–5 narrative folded into its commit-message header, then the diffstat and diff | The tool |
| Title | *Creation of new source code for &lt;topic&gt;* | The tool, from `topic` |
| Description (**255 characters**) | Elements 1–4 with short markers + scope figures | The tool, from your four fields |

So the **255-character field is the binding constraint**: keep each of the four
fields to one tight clause of **at most ~45 characters**. Longer text still
appears in full inside the patch header, but gets truncated in the description.

The tool also renames each patch after the result (`YYYY-MM-DD-<topic>-<sha>.patch`),
so your `topic` becomes the attachment name the Approver sees.

## Steps

1. **Ask which month to process.** Work out today's date, then ask the user to
   choose, showing the real `YYYY-MM` value in each bracket:

   - **A** — this month (`<current month>`)
   - **B** — previous month (`<previous month>`)
   - **C** — a different month they specify (`YYYY-MM`)

   Wait for the answer before continuing. Use the chosen month as `MONTH` below.

2. **Refresh the ledger and worklist.** Run in the terminal:

   ```bash
   .venv/bin/python commit_tracker.py monthly --month MONTH
   ```

   This syncs new commits, writes the reports, and emits **one** worklist at
   `data/ai/MONTH.worklist.json`.

3. **Find the worklist.** List `data/ai/*.worklist.json`. There should be
   exactly **one**. It contains **only items already flagged as likely creative
   Works**. Describe those items and nothing else — no review/config/revert
   rows. If the file is missing or `items` is empty, tell the user everything
   is already described (or there are no creative items) and stop.

4. **Describe each creative item.** For every item in the worklist:
   - Open the file named in its `patch` field (git `format-patch`: subject,
     body, full diff). Read it fully.
   - Ground every statement in that patch. **Do not invent** facts. Past tense.
   - Write **English and Polish** (same meaning) with these fields:
     - `topic` — noun phrase for the title *"Creation of new source code for
       &lt;topic&gt;"*. Not counted in the 255 limit (it is a separate field).
     - `created` — point 1: the problem this Work solves. Do **not** repeat the
       result name (it is already the attachment / `topic` title).
     - `relates_to` — point 2: specific program / application / component
       (names from the paths), not only the project.
     - `contribution` — point 3: what you personally did in this diff.
     - `creative_element` — point 4: your own concept vs routine/AI output.
   - Do **not** write point 5. The tool fills it from the patch.
   - The tool folds the full numbered narrative into the patch header:

     ```
     1. {problem it solves}
     2. What it relates to: {relates_to}
     3. My contribution. {contribution}
     4. Creative element: {creative_element}
     5. Evidence and scope: {patch file} — {n} file(s), +{ins}/−{del} lines.
     ```

   - …and the **255-character description** with short markers:

     ```
     Problem: {…} Component: {…} Mine: {…} Creative: {…} {n} file(s), +{ins}/-{del} lines.
     ```

     The markers plus scope use ~70 characters, leaving ~185 for your four
     fields combined — so **~45 characters each**. Write that tight. No "This
     commit…", no restating the title, no file lists in 1–4.

5. **Write the descriptions file** keyed by `work_key`:

   ```json
   {
     "pr:5729": {
       "en": {
         "topic": "datapath job schema alignment",
         "created": "Stopped config drift across datapath jobs.",
         "relates_to": "datapath jobs and schema models",
         "contribution": "Aligned YAML jobs and schema in this diff.",
         "creative_element": "One canonical schema instead of per-job copies."
       },
       "pl": {
         "topic": "…",
         "created": "…",
         "relates_to": "…",
         "contribution": "…",
         "creative_element": "…"
       }
     }
   }
   ```

6. **Import and rebuild:**

   ```bash
   .venv/bin/python commit_tracker.py describe --import data/ai/MONTH.descriptions.json
   .venv/bin/python commit_tracker.py report --html
   ```

7. **Summarize** how many **creative** items you described, the report paths,
   and the patch directory (`data/patches/YYYYMM/`) they upload from.
   Remind the user this is a **candidate list to review**, not a submission.
