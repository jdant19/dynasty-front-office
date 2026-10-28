# Dynasty Front Office — instructions for Claude sessions

This repo is Jordan's decision-support system for his 10-team salary-cap dynasty league
(team: Ja'Jah Pachulia). The design is in the doc "Dynasty Front Office — System Design"
(https://claude.ai/code/artifact/5a07d474-c40b-484b-84d3-5508fa52c135). Read it before structural changes.

## Non-negotiables
- Never submit, bid, propose, post or send anything on Jordan's behalf. Output recommendations and drafts only.
- Ask instead of assuming. Put open questions in the question queue; if a deadline cannot wait, show both branches.
- When Jordan pushes back, re-check the evidence and either hold the position with reasons or change it and say why.
- Every number carries its source and as-of time. Stale inputs are labeled and down-weighted, never silently used.
- Money is always whole dollars (round to nearest).

## Where things live
- `config/league.yaml` — the league rules as data. The rules engine reads it; tests check it. Never hard-code a rule elsewhere.
- `config/owners.yaml` — owners are the identity; team names are season aliases.
- `config/sources.yaml` — sources, tiers, routes, staleness limits.
- `config/settings.yaml` — objective and schedule settings.
- `data/raw/` — files exactly as received. Never edit; add new files with a date.
- `data/processed/` — clean tables built by `src/dfo/ingest`. Rebuild with `python -m dfo.ingest.build_history`.
- `data/snapshots/` — collector output (site snapshots, market values), each file stamped with its as-of time.
- `src/dfo/rules/` — cap, contract, tag and trade-legality math. Code, with tests.
- `.github/workflows/` — collector jobs that run on GitHub's servers (they reach sites the cloud cannot).

## How knowledge is stored (no one-off patches)
1. Rules — `config/league.yaml`, changed only when the league changes a rule.
2. Principles — `docs/principles.md`, each with its rationale and the evidence behind it.
3. Parameters — fit from data, stored with the fit date.
4. Observations — data, including Jordan's dated player views (scored like any source).
Classify every correction into one of these. Never add an unexplained exception to a prompt or a rule.

## Working conventions
- Decisions are scored on end states: the move plus its follow-on moves, against the best plan that skips it.
- Trade ideas are not open offers until Jordan says they were proposed (or the site shows them).
- In-season cap is worth close to nothing; next season's cap is real money.
- Commit messages say what changed and why.
