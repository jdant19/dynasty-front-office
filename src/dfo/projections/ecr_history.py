"""Recover weekly FantasyPros rankings for seasons the DynastyProcess database does not hold yet
(2025 onward) from the git history of DynastyProcess's fp_latest_weekly.csv.

For each NFL week, the last version committed before that week's Sunday is used.
Writes data/processed/ecr_weekly_recent.csv (same columns as ecr_weekly.csv).
"""
from __future__ import annotations

import io
import subprocess

import pandas as pd

from dfo.common import PROCESSED
from dfo.projections.ecr import PAGE_POS, TEAM_NICK, _week_calendar
from dfo.sources.fetch import CACHE

REPO = CACHE / "dynastyprocess"
FILE = "files/fp_latest_weekly.csv"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def build(since: str = "2025-08-01") -> pd.DataFrame:
    subprocess.run(["git", "-C", str(REPO), "fetch", "-q", f"--shallow-since={since}", "origin"], check=False)
    commits = [l.split() for l in _git("log", f"--since={since}", "--format=%H %cI", "--", FILE).splitlines()]
    commits = pd.DataFrame(commits, columns=["sha", "when"])
    commits["when"] = pd.to_datetime(commits.when, utc=True).dt.tz_convert("America/Chicago").dt.tz_localize(None)
    cal = _week_calendar()
    cal = cal[cal.season >= int(since[:4])]
    frames = []
    for _, wk in cal.iterrows():
        sunday = wk.first_game + pd.Timedelta(days=(6 - wk.first_game.weekday()) % 7)
        cands = commits[commits.when < sunday]
        if not len(cands) or cands.when.max() < sunday - pd.Timedelta(days=7):
            continue
        sha = cands.sort_values("when").iloc[-1].sha
        raw = pd.read_csv(io.StringIO(_git("show", f"{sha}:{FILE}")), low_memory=False)
        if "page_pos" in raw:          # current schema
            raw = raw.rename(columns={"fantasypros_id": "id", "player_name": "player"})
            raw["pos"] = raw.page_pos.replace({"DST": "DEF"})
        else:                          # older schema, same as db_fpecr
            raw = raw[raw.page_type.isin(PAGE_POS)].copy()
            raw["pos"] = raw.page_type.map(PAGE_POS)
        raw = raw[raw.pos.isin(["QB", "RB", "WR", "TE", "K", "DEF"])].copy()
        raw["season"], raw["week"] = int(wk.season), int(wk.week)
        raw["fp_proj"] = pd.to_numeric(raw.get("r2p_pts"), errors="coerce") if "r2p_pts" in raw else float("nan")
        frames.append(raw)
    raw = pd.concat(frames, ignore_index=True)
    raw["id"] = pd.to_numeric(raw.id, errors="coerce")
    raw["scrape_date"] = pd.to_datetime(raw.scrape_date)
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    fp = ids[ids.fantasypros_id.notna()][["fantasypros_id", "canonical_id"]]
    fp["fantasypros_id"] = fp.fantasypros_id.astype(float).astype(int)
    off = raw[raw.pos != "DEF"].merge(fp, left_on="id", right_on="fantasypros_id", how="inner")
    off["player_id"] = off.canonical_id
    dst = raw[raw.pos == "DEF"].copy()
    dst["player_id"] = "DEF-" + dst.player.map(
        lambda p: next((v for k, v in TEAM_NICK.items() if k in str(p)), None)).astype(str)
    out = pd.concat([off, dst[dst.player_id != "DEF-None"]], ignore_index=True)
    out = out.rename(columns={"sd": "ecr_sd"})[["season", "week", "player_id", "pos", "ecr", "ecr_sd", "scrape_date", "fp_proj"]]
    out.to_csv(PROCESSED / "ecr_weekly_recent.csv", index=False)
    return out


if __name__ == "__main__":
    e = build()
    print(len(e), "rows"); print(e.groupby(["season", "pos"]).week.nunique().unstack())
