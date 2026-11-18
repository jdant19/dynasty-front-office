# Front door and decision journal

The front door is one published page: https://claude.ai/artifact/3ck3di3wxuyLSpfkJnkVEw
(source: `frontdoor/front-office.html`, private to Jordan). It shows, in this order: the season odds
strip and stance, decisions due (agree / disagree and a reason), questions for Jordan (answer box),
a send box (notes, corrections, questions, trade offers received, screenshots) and data health.

## How data moves

1. The GitHub engine (`.github/workflows/engine.yml`) ends its daily, waivers and trades jobs with
   `python -m dfo.frontdoor.export`, which writes `data/frontdoor/brief.json` and appends any changed
   recommendation to `data/journal/recommendations.csv`.
2. GitHub's own schedule runs late (5 to 9 hours on 2026-11-15 to 17), so the scheduled Claude task
   "Front office sync" (about 6:41am and 3:41pm Central) starts the engine itself, waits for it, then
   runs the sync below. GitHub cannot write to the page; only a Claude session can.

## Page database

| Path | Written by | Contents |
|---|---|---|
| `brief/today` | the sync | `{json, made_at, week}`; `json` is brief.json as a string |
| `responses/{decision id}` | the page | `{id, verdict, reason, recommendation, at}`; one per decision, overwritten when Jordan changes his mind |
| `answers/{question id}` | the page | `{id, answer, at}` |
| `notes/{auto id}` | the page, then the sync | `{id, kind, text, asset_id, asset_ids, filename, at, status}`; the page writes `status: waiting`; the sync sets `status` (saved, entered, question) and a one-line `result`, which the page shows under "Recently sent" |

## The sync (what the scheduled task does)

0. Start the engine: `gh api -X POST repos/jdant19/dynasty-front-office/actions/workflows/engine.yml/dispatches
   -f ref=main -f inputs[job]=daily` (REST; `gh workflow run` uses GraphQL, which is blocked). Wait for that
   run to finish (`gh run list -w engine -L 1`, then `gh run watch <id>`, at most 20 minutes). If it cannot
   be started or fails, carry on with what is committed and say so in the summary.
1. `git pull`; note the engine run's result from `data/processed/run_log.csv`.
2. Read `responses`, `answers` and `notes` from the page database (ArtifactData list) and write them as
   `{"responses": [...], "answers": [...], "notes": [...]}` to a scratch file; run
   `PYTHONPATH=src python -m dfo.frontdoor.ingest <file>`. Rows already saved are skipped.
3. For each new note with files (`asset_ids`, or `asset_id` on older notes): download each (Artifact read
   with `path` = the asset id) into `data/raw/screenshots/2026/` as `<date>_<note id>_<n>.<ext>`, and look at
   it. A league scoreboard for a finished week is entered in `data/processed/results_2026.csv` (fill the
   scheduled row for each matchup) and Jordan's matchup page in `jordan_lineups_2026.csv` (every slot, Yahoo
   projection, points, status), the same way earlier weeks were, only when every number is legible and the
   starters add up to the total shown; otherwise a question goes in the queue. Skip a week already entered
   with the same numbers.
4. Every other new note is classified (rule, principle, parameter, observation: CLAUDE.md). Observations
   are recorded; anything that would change a rule, principle or parameter becomes a question to Jordan,
   never an automatic change. Set the note's status in `data/journal/notes.csv` and on its page document
   (ArtifactData update: `status` = entered / saved / question, `result` = one line on what was done).
5. If results or other inputs changed, re-run `dfo.sim.dial` and `dfo.decide.deadline` (Weeks 10-14), then
   `dfo.frontdoor.export`; then write `brief/today` with the new brief.json (pin `if_version` to the version
   just read).
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
