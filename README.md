# Dynasty Front Office

Decision support for a 10-team salary-cap dynasty fantasy football league: one shared engine for
projections, valuation, simulation and every in-season and offseason decision, with a learning loop
that grades its own calls.

Design: "Dynasty Front Office — System Design" (claude.ai doc). Build status follows its nine phases.

| Phase | Status |
| --- | --- |
| 1. Foundations (repo, site snapshot, first collector) | Done 2026-10-28: site snapshot every 4 hours, FantasyCalc daily |
| 2. Rules engine and data layer | Done 2026-10-28: rules core, history 2013-2026, one player ID map, league scoring 2013-2026 matching Yahoo on 239/239 player-weeks |
| 3. Projections | Done 2026-10-28: weekly, rest-of-season and 3-season projections, backtested 2021-2025 against FantasyPros (docs/projections.md) |
| 4. Valuation, including picks | Not started |
| 5. Simulator and dial | Not started |
| 6. History grading and owner models | Not started |
| 7. Decision modules | Not started |
| 8. Front door, scheduled runs, journal | Not started |
| 9. Shadow mode | Not started |

## Layout

```
config/            rules, owners, sources, settings (data, not code)
data/raw/          files exactly as received
data/processed/    clean history tables (rebuilt by src/dfo/ingest)
data/snapshots/    collector output, stamped with as-of time
src/dfo/           engine code
collectors/        scripts the GitHub collector jobs run
tests/             checks against the constitution and league history
.github/workflows/ scheduled collector jobs
```

## Rebuild the data

```
pip install -r requirements.txt
PYTHONPATH=src python -m dfo.ingest.build_history     # league history tables
PYTHONPATH=src python -m dfo.ingest.site              # current state from the latest site snapshot
PYTHONPATH=src python -m dfo.scoring 2026             # league scoring from nflverse (any season)
PYTHONPATH=src python -m dfo.ingest.player_ids        # one ID per player across sources
PYTHONPATH=src python -m dfo.reconcile                # computed points vs Yahoo
PYTHONPATH=src python -m pytest -q                    # or: python scripts/run_tests.py
```
