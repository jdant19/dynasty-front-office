"""Download helper for public data sources reachable from the cloud (nflverse, DynastyProcess, ffverse).

Files land in data/cache/<source>/ (not committed) with a sidecar .meta.json holding url and as-of time.
A cached file younger than max_age_hours is reused, so repeated runs do not hammer the source.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import urllib.request
from pathlib import Path

from dfo.common import ROOT

CACHE = ROOT / "data" / "cache"


def fetch(source: str, name: str, url: str, max_age_hours: float = 12) -> Path:
    d = CACHE / source
    d.mkdir(parents=True, exist_ok=True)
    path, meta = d / name, d / f"{name}.meta.json"
    if path.exists() and meta.exists():
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(json.loads(meta.read_text())["as_of"])
        if age.total_seconds() < max_age_hours * 3600:
            return path
    req = urllib.request.Request(url, headers={"User-Agent": "dynasty-front-office/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        path.write_bytes(r.read())
    meta.write_text(json.dumps({"url": url, "as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}))
    return path


def as_of(path: Path) -> str:
    meta = path.with_name(path.name + ".meta.json")
    return json.loads(meta.read_text())["as_of"] if meta.exists() else ""


NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"


def nflverse(tag: str, name: str, max_age_hours: float = 12) -> Path:
    return fetch("nflverse", name, f"{NFLVERSE}/{tag}/{name}", max_age_hours)


def dynastyprocess() -> Path:
    """Shallow, sparse clone of github.com/dynastyprocess/data (files/ only); refreshed when older than 12h."""
    d = CACHE / "dynastyprocess"
    stamp = d / ".as_of"
    fresh = stamp.exists() and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
        stamp.read_text().strip())).total_seconds() < 12 * 3600
    if not fresh:
        if (d / ".git").exists():
            subprocess.run(["git", "-C", str(d), "pull", "-q", "--depth", "1"], check=True)
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse",
                            "https://github.com/dynastyprocess/data", str(d)], check=True)
            subprocess.run(["git", "-C", str(d), "sparse-checkout", "set", "files"], check=True)
        stamp.write_text(dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    return d / "files"
