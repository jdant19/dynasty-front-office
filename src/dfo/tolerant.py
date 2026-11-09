"""Run a module, treating a source outage as a skipped step rather than a failure.

    PYTHONPATH=src python -m dfo.tolerant dfo.scoring 2026

Used by CI for rebuild steps that download public data: when a source is down (and no cached copy
exists), the step prints a GitHub warning and the committed table is kept; any other error still fails.
"""
import runpy
import sys

from dfo.sources.fetch import SourceUnavailable

if __name__ == "__main__":
    mod, sys.argv = sys.argv[1], [sys.argv[1]] + sys.argv[2:]
    try:
        runpy.run_module(mod, run_name="__main__", alter_sys=True)
    except SourceUnavailable as e:
        print(f"::warning::{mod} skipped, source unavailable ({e}); the committed copy is kept")
