"""FantasyPros rankings as owners saw them at each offseason event (config/league.yaml offseason_calendar):
the auction (mid-to-late August -> last scrape on or before Aug 25) and RFA bidding and the rookie
draft (June-July -> on or before Jul 1).

Per year: redraft positional ranks ('rp'), dynasty positional ranks ('dp') and rookie ranks ('drk'). 2020-2024 come from the DynastyProcess database; 2025 onward from the git
history of its db_fpecr_latest.csv (the database file stops in early August 2025).

    PYTHONPATH=src python -m dfo.valuation.preseason     # writes data/processed/ranks_at_{auction,rfa}.csv
"""
from __future__ import annotations

import io
import subprocess

import pandas as pd

from dfo.common import PROCESSED
from dfo.projections.ecr import TEAM_NICK
from dfo.sources.fetch import CACHE, dynastyprocess

CUTOFFS = {"auction": "08-25", "rfa": "07-01", "rookie_draft": "07-01"}   # month-day
REPO = CACHE / "dynastyprocess"
LATEST = "files/db_fpecr_latest.csv"
POS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def _select(d: pd.DataFrame) -> pd.DataFrame:
    """Keep redraft positional, dynasty positional and rookie ranks, tagged as 'rp', 'dp', 'drk'.
    Selected by rank type and position, not page name: in Aug 2020 the database's page names are
    wrong (dynasty ranks sit under 'weekly-offense') and redraft positional ranks are typed 'wp'."""
    d = d.copy()
    d["pos"] = d.pos.replace({"DST": "DEF", "D/ST": "DEF"})
    d = d[d.pos.isin(POS) & ~d.page_type.astype(str).str.contains("idp|dl|lb|db", regex=True)]
    redraft = d.page_type.astype(str).str.startswith("redraft") & d.ecr_type.isin(["rp", "wp"])
    d.loc[redraft, "ecr_type"] = "rp"
    return d[redraft | (d.ecr_type == "dp") | (d.ecr_type == "drk")]


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def _from_db(years: list[int], cutoff: str) -> pd.DataFrame:
    d = pd.read_csv(dynastyprocess() / "db_fpecr.csv.gz", low_memory=False,
                    usecols=["page_type", "ecr_type", "id", "player", "pos", "ecr", "sd", "scrape_date"])
    d = _select(d)
    d["scrape_date"] = pd.to_datetime(d.scrape_date)
    out = []
    for y in years:
        x = d[(d.scrape_date.dt.year == y) & (d.scrape_date <= pd.Timestamp(f"{y}-{cutoff}"))]
        for t, g in x.groupby("ecr_type"):
            out.append(g[g.scrape_date == g.scrape_date.max()].assign(season=y))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _from_git(year: int, cutoff: str) -> pd.DataFrame:
    subprocess.run(["git", "-C", str(REPO), "fetch", "-q", f"--shallow-since={year}-05-01", "origin"], check=False)
    log = _git("log", f"--since={year}-05-01", f"--until={year}-{cutoff} 23:59", "--format=%H %cI", "--", LATEST)
    if not log.strip():
        return pd.DataFrame()
    sha = log.splitlines()[0].split()[0]          # newest first
    d = pd.read_csv(io.StringIO(_git("show", f"{sha}:{LATEST}")), low_memory=False)
    d = _select(d)
    d["scrape_date"] = pd.to_datetime(d.scrape_date)
    return d.assign(season=year)[["page_type", "ecr_type", "id", "player", "pos", "ecr", "sd", "scrape_date", "season"]]


def build(event: str = "auction", years=range(2020, 2027)) -> pd.DataFrame:
    cutoff = CUTOFFS[event]
    years = list(years)
    db = _from_db([y for y in years if y <= 2024], cutoff)
    git = [_from_git(y, cutoff) for y in years if y >= 2025]
    raw = pd.concat([db] + git, ignore_index=True)
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    fp = ids[ids.fantasypros_id.notna()][["fantasypros_id", "canonical_id"]].copy()
    fp["fantasypros_id"] = fp.fantasypros_id.astype(float).astype(int)
    raw["id"] = pd.to_numeric(raw.id, errors="coerce")
    off = raw[raw.pos != "DEF"].merge(fp, left_on="id", right_on="fantasypros_id", how="inner")
    off["player_id"] = off.canonical_id
    dst = raw[raw.pos == "DEF"].copy()
    dst["player_id"] = "DEF-" + dst.player.map(
        lambda p: next((v for k, v in TEAM_NICK.items() if k in str(p)), None)).astype(str)
    out = pd.concat([off, dst[dst.player_id != "DEF-None"]], ignore_index=True)
    out = out.pivot_table(index=["season", "player_id", "pos"], columns="ecr_type", values="ecr", aggfunc="min").reset_index()
    dates = raw.groupby(["season", "ecr_type"]).scrape_date.max().unstack()
    out = out.rename(columns={"rp": "redraft_rank", "dp": "dynasty_rank", "drk": "rookie_rank"})
    for t, c in [("rp", "redraft_as_of"), ("dp", "dynasty_as_of"), ("drk", "rookie_as_of")]:
        out[c] = out.season.map(dates[t]) if t in dates else pd.NaT
    out.insert(0, "event", event)
    out.to_csv(PROCESSED / f"ranks_at_{event}.csv", index=False)
    return out


if __name__ == "__main__":
    for ev in ["auction", "rfa"]:          # rookie draft shares the RFA date
        o = build(ev)
        print(ev); print(o.groupby("season").agg(redraft=("redraft_rank", "count"), dynasty=("dynasty_rank", "count"),
                                              rookie=("rookie_rank", "count"), r_as_of=("redraft_as_of", "first"),
                                              d_as_of=("dynasty_as_of", "first")).to_string())
