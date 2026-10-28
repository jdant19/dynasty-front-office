"""Collector: read-only snapshot of the league site's database (Supabase). Runs on GitHub's servers.

Preferred route: a read-only export function on the site's backend (Lovable Cloud), called with two
GitHub secrets set by Jordan: LEAGUE_EXPORT_URL and LEAGUE_EXPORT_TOKEN. The function only returns the
tables it allows. Fallback route: Supabase REST with SUPABASE_URL and SUPABASE_READ_KEY (publishable
key; never the service-role key). Tables to export are listed in config/site.yaml.
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
    """Tables listed under `tables:` in config/site.yaml, minus anything under `never:`.
    (Parsed by hand so the job needs no installs.)"""
    cfg = ROOT / "config" / "site.yaml"
    section, wanted, banned = None, [], set()
    for raw in cfg.read_text().splitlines():
        line = raw.split("#")[0].rstrip()
        if not line.strip():
            continue
        if not raw.startswith(" ") and line.endswith(":"):
            section = line[:-1].strip()
            continue
        item = line.strip()
        if item.startswith("- "):
            (wanted.append if section == "tables" else banned.add)(item[2:].strip())
    return [t for t in wanted if t not in banned]


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


def fetch_export(url: str, token: str, names: list[str]) -> dict:
    req = urllib.request.Request(f"{url}?tables={','.join(names)}",
                                 headers={"x-export-token": token, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))["tables"]


def main() -> int:
    names = tables()
    exp_url, exp_token = os.environ.get("LEAGUE_EXPORT_URL"), os.environ.get("LEAGUE_EXPORT_TOKEN")
    base, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_READ_KEY")
    if not names or not ((exp_url and exp_token) or (base and key)):
        print("site snapshot not configured yet: need LEAGUE_EXPORT_URL + LEAGUE_EXPORT_TOKEN "
              "(or SUPABASE_URL + SUPABASE_READ_KEY) and tables in config/site.yaml")
        return 0
    exported = fetch_export(exp_url, exp_token, names) if exp_url and exp_token else None
    now = dt.datetime.now(dt.timezone.utc)
    stamp = OUT / f"{now:%Y-%m-%dT%H%M}"
    latest = OUT / "latest"
    for d in (stamp, latest):
        d.mkdir(parents=True, exist_ok=True)
    failures = 0
    for t in names:
        try:
            data = exported[t] if exported is not None else fetch(base, key, t)
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
