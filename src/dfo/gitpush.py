"""Commit and push from a scheduled job without failing when another job pushed first.

    python -m dfo.gitpush -m "engine: daily ..." data/processed data/frontdoor data/journal

The collector, the engine, the sync and the analyst all push to main, often minutes apart. A plain push
is rejected when another job got there first (2026-11-22: the collector failed that way). This pulls with
rebase and retries. Where both sides changed a file: append-only tables (data/journal/*.csv,
run_log.csv, sim_2026_history.csv) keep the rows of both; any other generated file keeps this job's
version, the newer build.
"""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
import time

APPEND_ONLY = ("data/journal/", "data/processed/run_log.csv", "data/processed/sim_2026_history.csv")


def git(*a: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *a], capture_output=True, text=True, check=check)


def _union(path: str) -> None:
    """Both sides' rows of an append-only CSV (stage 2: upstream, stage 3: this job's commit), upstream
    first, each line once. Text-level, so it needs nothing beyond git (the collector job has no pandas)."""
    seen, out = set(), []
    for stage in ("2", "3"):
        r = git("show", f":{stage}:{path}", check=False)
        if r.returncode != 0:
            continue
        lines = r.stdout.splitlines()
        if not out and lines:
            out.append(lines[0])
            seen.add(lines[0])
        for ln in lines[1:]:
            if ln not in seen:
                seen.add(ln)
                out.append(ln)
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


def resolve() -> bool:
    """Resolve the conflicts of the rebase step in progress; False if something is left unresolved."""
    files = [f for f in git("diff", "--name-only", "--diff-filter=U").stdout.split() if f]
    for f in files:
        if f.endswith(".csv") and f.startswith(APPEND_ONLY):
            _union(f)
        else:
            git("checkout", "--theirs", "--", f)   # during a rebase, "theirs" is the commit being replayed: ours
        git("add", "--", f)
    return not git("diff", "--name-only", "--diff-filter=U").stdout.strip()


def main(message: str, paths: list[str], tries: int = 5) -> int:
    git("add", "--", *paths)
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("no changes")
        return 0
    git("commit", "-q", "-m", message)
    for k in range(tries):
        r = git("pull", "--rebase", "--autostash", "-q", check=False)   # other changed files are set aside, not a reason to fail
        while r.returncode != 0 and git("rev-parse", "-q", "--verify", "REBASE_HEAD", check=False).returncode == 0:
            if not resolve():
                git("rebase", "--abort", check=False)
                break
            r = subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"], capture_output=True, text=True)
        if r.returncode == 0 and git("push", "-q", check=False).returncode == 0:
            print(f"pushed (attempt {k + 1})")
            return 0
        time.sleep(5 + random.random() * 15)
    print("could not push after retries", file=sys.stderr)
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--message", required=True)
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    sys.exit(main(a.message, a.paths))
