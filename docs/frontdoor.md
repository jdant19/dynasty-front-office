# Front door and decision journal

The front door is one published page, in four tabs: Today (decisions one line each, opened for the
detail; questions; discussions; send), This season (Jordan's remaining matchups with win chances, his odds
week by week, league standings and outlook), Future (cap and strength by year, the offseason plan and its
alternatives, the contract grid, picks) and Data (data health). It is: https://claude.ai/artifact/3ck3di3wxuyLSpfkJnkVEw
(source: `frontdoor/front-office.html`, private to Jordan). The season and future tabs come from
`dfo.frontdoor.views` (season.json, future.json, folded into the brief by the export); the odds trend
is a replay cached in `data/processed/sim_2026_trend.csv` (one row per finished week).

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
| `threads/adhoc-<YYYYMMDD>-<slug>` | any Claude session | an ad-hoc question Jordan raised in a session on the repo and its answer; shown under "Discussions" (CLAUDE.md, "Ad-hoc questions") |
| `threads/{decision id}` | the page, then the sync | `{id, title, recommendation, messages: [{role: you / claude / sync, text, at}], status: open / requested / answered, requested_at}`; "Discuss" on a decision card. "Ask now" answers on the page from the brief alone; "Send to the sync" sets `status: requested` |
| `threads/strat-<YYYYMMDDhhmmss>` | the Strategy tab, the analyst, the sync | an open strategy conversation: `{id, title, messages: [{role: you / claude / analyst / sync, text, at}], status, requested_at, handoff: code / sync}` |
| `strategy_notes/{id}` | the Strategy tab | something Jordan asked to remember or a conclusion he confirmed: `{id, at, source, topic, note, status}`; journaled to `data/journal/strategy_notes.csv` |
| `pack/players`, `pack/teams`, `pack/memory`, `pack/rules` | the sync, the analyst | the league data pack and the strategy memory (`dfo.frontdoor.pack`), each `{json: <file as a string>}` |
| `requests/R<YYYYMMDDhhmmss>` | the page, then the refresh session | an on-demand re-run: `{id, job: everything / lineup / waivers / trades, card, card_title, note, status: starting / started / queued / running / done / failed, at, started_at, finished_at, summary, why}` |
| `notes/{auto id}` | the page, then the sync | `{id, kind, text, asset_id, asset_ids, filename, at, status}`; the page writes `status: waiting`; the sync sets `status` (saved, entered, question) and a one-line `result`, which the page shows under "Recently sent" |

## The sync (what the scheduled task does)

0. Refresh the league site first, then the engine. The engine reads rosters, transactions and cap from the
   collector's latest committed snapshot, and GitHub's own collector schedule runs hours late, so a sync
   that only starts the engine misses moves Jordan made since (2026-11-18: his Ravens-for-Lions swap).
   a. `gh api -X POST repos/jdant19/dynasty-front-office/actions/workflows/collect.yml/dispatches -f ref=main`;
      wait for that run (`gh run list -w collect -L 1`, then poll `gh run view <id>`), at most 10 minutes.
   b. `gh api -X POST repos/jdant19/dynasty-front-office/actions/workflows/engine.yml/dispatches -f ref=main
      -f inputs[job]=daily`; wait for that run the same way, at most 20 minutes. (REST: `gh workflow run`
      uses GraphQL, which is blocked.) Dispatch each once. If either cannot be started or fails, carry on
      with what is committed and say so in the summary.
1. `git pull`; note the engine run's result from `data/processed/run_log.csv`.
2. Read `responses`, `answers`, `notes`, `threads` and `strategy_notes` from the page database (ArtifactData list) and write them as
   `{"responses": [...], "answers": [...], "notes": [...], "threads": [...], "strategy_notes": [...]}` to a scratch file; run
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
4b. Threads with `status: requested` (Discuss → "Send to the sync"): re-check the decision with the full
   data, as CLAUDE.md asks for pushback (re-run the module or the evaluation behind it, look at the inputs
   Jordan questions), then either hold with reasons or change it and say why. If the fault is in the
   system, fix it (classify it: rule, principle, parameter, observation) and re-run. Reply by appending
   `{role: "sync", text, at}` to the thread's messages and setting `status: "answered"` (ArtifactData
   update pinned to the version read). Add the threads to the ingest dump (`"threads": [...]`) so every
   message lands in `data/journal/threads.csv`.
4c. Requests with `status: queued` (a Re-run the page could not start): do them as the "Front office refresh"
   task would (its prompt: re-run the job, check the note, mark done with a summary).
5. If results or other inputs changed, re-run `dfo.sim.dial` and `dfo.decide.deadline` (Weeks 10-14), then
   `dfo.frontdoor.views` (season and future tabs), `dfo.frontdoor.pack` (Strategy tab data and memory) and
   `dfo.frontdoor.export`; write the four `pack/*` documents (one ArtifactData batch, each pinned to the
   version read); then write `brief/today` with the new brief.json (pin `if_version` to the version
   just read).
6. Commit `data/journal`, `data/frontdoor`, `data/raw` and any processed tables with a message saying
   what came in; push.

Nothing in the sync proposes, bids or submits anything in the league.

## Strategy tab

Open strategy conversations. The page's Claude answers at once with tools over the data pack (every
rostered player and the best free agents with contracts and values, all ten teams with odds, cap, picks,
tendencies and observations, the league rules, the brief) and starts from the strategy memory (Jordan's
plan, standing strategy notes, his outlook). It cannot re-run models: "Run it in the models" marks the
conversation `status: requested` and starts the "Front office analyst" scheduled task through the Claude
Code connector (`fire_trigger`, trigger `trig_011UZ6QdFFvH937GqyCF2uVi`, with the thread id). That Code
session reads the conversation, re-runs what the question needs, replies as `role: analyst`, and keeps
any settled conclusion in `data/journal/strategy_notes.csv` (and the page's `pack/memory`). If the page
cannot start a session (no connector, no consent) the conversation waits for the next sync (step 4b).
"Remember…" writes a strategy note directly.

## Re-run

"Re-run now…" (top of Today) and "Re-run this" (each decision card) write a `requests/*` document and start
the "Front office refresh" scheduled task (trigger `trig_01KEU8RQEGqjHMSidZi4ssoJ`) through the Claude Code
connector. That session refreshes the league site and runs the engine job (`daily` for everything, else
`lineup`, `waivers` or `trades`), checks the optional "what looks off" note against the data (fixing the
system with a test when it is at fault), rebuilds views, pack and brief, writes them to the page, and
marks the request done with a short summary (also posted to the card's thread). If the page cannot start
a session the request is `queued` and the next sync runs it (step 4c).

## Decision journal (`data/journal/`)

| File | One row per |
|---|---|
| `recommendations.csv` | a recommendation the first time it is made or whenever its text changes |
| `responses.csv` | each agree / disagree Jordan records (a changed verdict is a new row) |
| `questions.csv` | each question in the queue, both directions, with its answer |
| `notes.csv` | each note or upload from the page, with what was done with it |
| `threads.csv` | each message in a Discuss thread or Strategy conversation (Jordan, the page's Claude, the analyst, the sync) |
| `strategy_notes.csv` | each standing strategy note (asked to remember, or settled by the analyst); `status` active or retired |

All four are append-only. Following (what Jordan actually started, bid and traded against the
recommendation) is matched from `jordan_lineups_2026.csv`, the site transactions and trades; the
weekly follow report is the next step.
