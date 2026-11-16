# Front door and decision journal

The front door is one published page: https://claude.ai/artifact/3ck3di3wxuyLSpfkJnkVEw
(source: `frontdoor/front-office.html`, private to Jordan). It shows, in this order: the season odds
strip and stance, decisions due (agree / disagree and a reason), questions for Jordan (answer box),
a send box (notes, corrections, questions, trade offers received, screenshots) and data health.

## How data moves

1. The GitHub engine (`.github/workflows/engine.yml`) ends its daily, waivers and trades jobs with
   `python -m dfo.frontdoor.export`, which writes `data/frontdoor/brief.json` and appends any changed
   recommendation to `data/journal/recommendations.csv`.
2. The daily scheduled Claude task ("Front office sync", about 6:41am Central, after the engine) runs
   the sync below. GitHub cannot write to the page; only a Claude session can.

## Page database

| Path | Written by | Contents |
|---|---|---|
| `brief/today` | the sync | `{json, made_at, week}`; `json` is brief.json as a string |
| `responses/{decision id}` | the page | `{id, verdict, reason, recommendation, at}`; one per decision, overwritten when Jordan changes his mind |
| `answers/{question id}` | the page | `{id, answer, at}` |
| `notes/{auto id}` | the page | `{id, kind, text, asset_id, filename, at}`; `asset_id` points to an uploaded file |

## The sync (what the scheduled task does)

1. `git pull`; check `data/processed/run_log.csv` for this morning's engine run (note it if missing or failed).
2. Read `responses`, `answers` and `notes` from the page database (ArtifactData list) and write them as
   `{"responses": [...], "answers": [...], "notes": [...]}` to a scratch file; run
   `PYTHONPATH=src python -m dfo.frontdoor.ingest <file>`. Rows already saved are skipped.
3. For each new note with an `asset_id`: download it (Artifact read with `path` = the asset id) into
   `data/raw/screenshots/2026/` as `<date>_<note id>_<filename>`. A league scoreboard for a finished week
   is added to `results_2026.csv` and Jordan's lineup to `jordan_lineups_2026.csv` the same way earlier
   weeks were, only when every number is legible; otherwise a question goes in the queue.
4. Every other new note is classified (rule, principle, parameter, observation: CLAUDE.md). Observations
   are recorded; anything that would change a rule, principle or parameter becomes a question to Jordan,
   never an automatic change. Notes are marked `status=done` (or `question`) in `data/journal/notes.csv`.
5. If anything changed inputs, re-run `dfo.frontdoor.export`; then write `brief/today` with the new
   brief.json (pin `if_version` to the version just read).
6. Commit `data/journal`, `data/frontdoor`, `data/raw` and any processed tables with a message saying
   what came in; push.

Nothing in the sync proposes, bids or submits anything in the league.

## Decision journal (`data/journal/`)

| File | One row per |
|---|---|
| `recommendations.csv` | a recommendation the first time it is made or whenever its text changes |
| `responses.csv` | each agree / disagree Jordan records (a changed verdict is a new row) |
| `questions.csv` | each question in the queue, both directions, with its answer |
| `notes.csv` | each note or upload from the page, with what was done with it |

All four are append-only. Following (what Jordan actually started, bid and traded against the
recommendation) is matched from `jordan_lineups_2026.csv`, the site transactions and trades; the
weekly follow report is the next step.
