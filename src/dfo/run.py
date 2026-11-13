"""Scheduled engine runs (Phase 8): one command per job, safe to run unattended.

    PYTHONPATH=src python -m dfo.run daily       # every morning
    PYTHONPATH=src python -m dfo.run waivers     # Monday night, after the last game
    PYTHONPATH=src python -m dfo.run lineup      # game-day mornings (Thursday, Sunday, Monday)

Each step runs in order; a step whose source is down is skipped with a warning and the committed copy
kept (dfo.tolerant); any other failure stops the job so a broken run never overwrites good outputs
with partial ones. Every run appends a line to data/processed/run_log.csv: job, step, status, seconds,
time. The calendar decides which modules run (design section 8): the trade search and deadline posture
in Weeks 10-14, waivers in Weeks 1-18, start/sit in Weeks 1-18.
"""
from __future__ import annotations

import datetime as dt
import runpy
import sys
import time
import traceback

import pandas as pd

from dfo.common import PROCESSED
from dfo.sources.fetch import SourceUnavailable

DEADLINE_WEEKS = range(10, 15)
SEASON_WEEKS = range(1, 19)


def _week() -> int:
    try:
        return int(pd.read_csv(PROCESSED / "proj_week.csv").week.iloc[0])
    except Exception:
        return 0


def _step(module: str, *args: str) -> tuple[str, str]:
    argv = sys.argv
    sys.argv = [module, *args]
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return "ok", ""
    except SourceUnavailable as e:
        print(f"::warning::{module} skipped, source unavailable ({e})")
        return "skipped: source down", str(e)
    except SystemExit as e:
        return ("ok", "") if not e.code else ("failed", f"exit {e.code}")
    finally:
        sys.argv = argv


def plan(job: str) -> list[tuple]:
    refresh = [("dfo.ingest.site",), ("dfo.scoring", "2026"), ("dfo.projections.current",)]
    if job == "daily":
        steps = refresh + [("dfo.sim.dial",), ("dfo.sim.future",), ("dfo.decide.lineup",)]
        steps += [("dfo.decide.deadline",)] if _week() in DEADLINE_WEEKS else []
        return steps
    if job == "waivers":
        return refresh + [("dfo.decide.waivers",)]
    if job == "lineup":
        return refresh + [("dfo.decide.lineup",)]
    if job == "trades":
        # weekly: re-fit valuation (prices, parameters) before searching for trades
        return refresh + [("dfo.valuation.build",), ("dfo.sim.dial",), ("dfo.sim.future",), ("dfo.decide.owner_plans",),
                          ("dfo.decide.trade_search", "--top", "6", "--sims", "3000")]
    raise SystemExit(f"unknown job {job!r}: daily, waivers, lineup, trades")


def main(job: str) -> int:
    log = PROCESSED / "run_log.csv"
    rows, failed = [], False
    for step in plan(job):
        t0 = time.time()
        try:
            status, note = _step(*step)
        except Exception:
            status, note = "failed", traceback.format_exc(limit=3).strip().splitlines()[-1]
        rows.append(dict(time=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), job=job,
                         step=" ".join(step), status=status, seconds=round(time.time() - t0), note=note[:200]))
        print(f"[{status}] {' '.join(step)} ({rows[-1]['seconds']}s) {note[:120]}", flush=True)
        if status == "failed":
            failed = True
            break
    old = pd.read_csv(log) if log.exists() else pd.DataFrame()
    pd.concat([old, pd.DataFrame(rows)], ignore_index=True).tail(2000).to_csv(log, index=False)
    return 1 if failed else 0


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "daily"))
