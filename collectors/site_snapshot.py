"""Collector: read-only snapshot of the league site's database (Supabase). Runs on GitHub's servers.

Needs two GitHub secrets, set by Jordan: SUPABASE_URL and SUPABASE_READ_KEY (the public anon key or a
read-only key; never the service-role key). Tables to export are listed in config/site.yaml.
Writes data/snapshots/site/<YYYY-MM-DDTHHMM>/<table>.json plus data/snapshots/site/latest/.
Roster changes between snapshots are turned into transaction records by the engine, not here.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "snapshots" / "site"


def tables() -> list[str]:
    cfg = ROOT / "config" / "site.yaml"
    names = []
    for line in cfg.read_text().splitlines():
        line = line.strip()
        if line.startswith("- ") and not line.startswith("- #"):
            names.append(line[2:].split("#")[0].strip())
    return [n for n in names if n]


def fetch(base: str, key: str, table: str) -> list:
    rows, start, page = [], 0, 1000
    while True:
        req = urllib.request.Request(
            f"{base.rstrip('/')}/rest/v1/{table}?select=*",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Range": f"{start}-{start + page - 1}", "Range-Unit": "items"})
        with urllib.request.urlopen(req, timeout=60) as r:
            batch = json.loads(r.read().decode("utf-8"))
        rows.extend(batch)
        if len(batch) < page:
            return rows
        start += page


def main() -> int:
    base, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_READ_KEY")
    names = tables()
    if not base or not key or not names:
        print("site snapshot not configured yet: need SUPABASE_URL, SUPABASE_READ_KEY and tables in config/site.yaml")
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    stamp = OUT / f"{now:%Y-%m-%dT%H%M}"
    latest = OUT / "latest"
    for d in (stamp, latest):
        d.mkdir(parents=True, exist_ok=True)
    failures = 0
    for t in names:
        try:
            data = fetch(base, key, t)
        except Exception as e:
            print(f"{t}: FAILED {e}", file=sys.stderr)
            failures += 1
            continue
        payload = json.dumps({"table": t, "as_of": now.isoformat(timespec="seconds"), "rows": data}, indent=1)
        (stamp / f"{t}.json").write_text(payload)
        (latest / f"{t}.json").write_text(payload)
        print(f"{t}: {len(data)} rows")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
