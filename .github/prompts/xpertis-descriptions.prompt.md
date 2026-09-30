---
mode: agent
description: One-button monthly run — sync commits, then use Copilot to write the bilingual Xpertis "Work" descriptions for each creative patch and fold them back into the report.
---

# Generate the monthly Xpertis creative-work descriptions

You are completing the AI step of the `commit_tracker` tool. Everything happens
**locally in this workspace** — do not send patch contents to any external
service or fetch any URL. Work only from files on disk.

Use `.venv/bin/python` if it exists, otherwise `python3`.

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

   This syncs new commits, writes the deterministic reports, and emits **one**
   worklist for that month at `data/ai/MONTH.worklist.json`.

3. **Find the worklist.** List `data/ai/*.worklist.json`. By design there should
   be exactly **one** (the month chosen above) — process only that file. It has a
   `month`, an `output_file` path, and an `items` array. If there are none, tell
   the user everything is already described for that month and stop.

4. **Describe each item.** For every item in the worklist:
   - Open the file named in its `patch` field. It is a git `format-patch`, so it
     contains the commit **subject**, the **message body**, and the **full
     diff** — everything you need. Read it fully.
   - Ground every statement in that patch. **Do not invent** facts the diff does
     not support. Be factual and past-tense.
   - Write, in **English and Polish** (equivalent content), these fields:
     - `topic` — a short noun phrase that completes the title *"Creation of new
       source code for &lt;topic&gt;"* (e.g. `refactor of datapath job
       configurations and schema definitions for consistency and optimization`).
       No trailing period.
     - `created` — **what was created**: the name/result and the problem it
       solves.
     - `relates_to` — **what it relates to**: the specific program, application
       or component (module / model / pipeline names from the paths), not just
       the project.
     - `contribution` — **your contribution**: what the author personally did —
       the concrete modules, models or functionality in this diff. For
       co-authored commits, describe only the parts in this diff.
     - `creative_element` — **the creative element**: which part reflects the
       author's own concept rather than a ready-made pattern, standard, or
       routine step. Where AI tooling was plausibly used, describe the human
       intellectual contribution *beyond* the generated output.

   Do not write point 5 (evidence & scope) — the tool computes it from the patch.

5. **Write the descriptions file.** Create the file named in the worklist's
   `output_file` field (e.g. `data/ai/2026-09.descriptions.json`) as a JSON
   object **keyed by each item's `work_key`**:

   ```json
   {
     "pr:5729": {
       "en": {
         "topic": "…",
         "created": "…",
         "relates_to": "…",
         "contribution": "…",
         "creative_element": "…"
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

6. **Import and rebuild the report.** Import the descriptions file, then rebuild
   the reports so the descriptions appear:

   ```bash
   .venv/bin/python commit_tracker.py describe --import data/ai/MONTH.descriptions.json
   .venv/bin/python commit_tracker.py report --html
   ```

7. **Summarize.** Report how many items you described and the paths of the
   updated reports under `data/reports/`. Remind the user this is a
   **candidate list to review**, not an Xpertis submission.
