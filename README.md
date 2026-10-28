# Dynasty Front Office

Decision support for a 10-team salary-cap dynasty fantasy football league: one shared engine for
projections, valuation, simulation and every in-season and offseason decision, with a learning loop
that grades its own calls.

Design: "Dynasty Front Office — System Design" (claude.ai doc). Build status follows its nine phases.

| Phase | Status |
| --- | --- |
| 1. Foundations (repo, site snapshot, first collector) | Done 2026-10-28: site snapshot every 4 hours, FantasyCalc daily |
| 2. Rules engine and data layer | History tables and rules core built and tested; player ID map and scoring reconciliation next |
| 3. Projections | Not started |
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

## Rebuild the history tables

```
pip install -r requirements.txt
PYTHONPATH=src python -m dfo.ingest.build_history
PYTHONPATH=src python -m pytest -q
```
