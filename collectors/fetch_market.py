"""Collector: market values from sources the cloud cannot reach. Runs on GitHub's servers.

Writes data/snapshots/market/<source>/<YYYY-MM-DD>.json plus latest.json, each with an as_of stamp.
Standard library only, so the job needs no installs.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "snapshots" / "market"

SOURCES = {
    # 1QB, 10 teams, half PPR dynasty values (public API)
    "fantasycalc": "https://api.fantasycalc.com/values/current?isDynasty=true&numQbs=1&numTeams=10&ppr=0.5",
}


def fetch(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "dynasty-front-office-collector/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    failures = 0
    for name, url in SOURCES.items():
        try:
            data = fetch(url)
        except Exception as e:  # keep going; one source failing must not block the others
            print(f"{name}: FAILED {e}", file=sys.stderr)
            failures += 1
            continue
        payload = {"source": name, "url": url, "as_of": now.isoformat(timespec="seconds"), "data": data}
        d = OUT / name
        d.mkdir(parents=True, exist_ok=True)
        for path in (d / f"{now:%Y-%m-%d}.json", d / "latest.json"):
            path.write_text(json.dumps(payload, indent=1))
        n = len(data) if isinstance(data, list) else "?"
        print(f"{name}: {n} records")
    return 1 if failures == len(SOURCES) else 0


if __name__ == "__main__":
    sys.exit(main())
