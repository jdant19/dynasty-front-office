"""FantasyPros expert consensus rankings (via the DynastyProcess mirror), mapped to NFL weeks and ids.

Weekly rankings are scraped on Fridays (sometimes Thursdays); a scrape belongs to the NFL week whose
Sunday follows it. Players whose game kicked off before the scrape are dropped from that week.
"""
from __future__ import annotations

import pandas as pd

from dfo.common import PROCESSED
from dfo.sources.fetch import dynastyprocess, nflverse

PAGE_POS = {"weekly-qb": "QB", "weekly-rb": "RB", "weekly-wr": "WR", "weekly-te": "TE",
            "weekly-k": "K", "weekly-dst": "DEF"}
FP_TEAM = {"JAC": "JAX", "LAR": "LA", "LVR": "LV", "OAK": "LV", "SD": "LAC", "STL": "LA", "WSH": "WAS"}
TEAM_NICK = {"Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF", "Panthers": "CAR",
             "Bears": "CHI", "Bengals": "CIN", "Browns": "CLE", "Cowboys": "DAL", "Broncos": "DEN",
             "Lions": "DET", "Packers": "GB", "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX",
             "Chiefs": "KC", "Rams": "LA", "Chargers": "LAC", "Raiders": "LV", "Dolphins": "MIA",
             "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG", "Jets": "NYJ",
             "Eagles": "PHI", "Steelers": "PIT", "Seahawks": "SEA", "49ers": "SF", "Buccaneers": "TB",
             "Titans": "TEN", "Commanders": "WAS", "Football Team": "WAS", "Redskins": "WAS"}


def _week_calendar() -> pd.DataFrame:
    g = pd.read_csv(nflverse("schedules", "games.csv"), low_memory=False)
    g = g[g.game_type == "REG"]
    g["gameday"] = pd.to_datetime(g.gameday)
    cal = g.groupby(["season", "week"]).gameday.agg(["min", "max"]).reset_index()
    return cal.rename(columns={"min": "first_game", "max": "last_game"})


def weekly_ecr() -> pd.DataFrame:
    """Historical weekly ranks (2020-2024, DynastyProcess database) plus recent seasons recovered
    from DynastyProcess git history (2025 onward, see ecr_history.py), which also carry FantasyPros'
    own projected points (fp_proj)."""
    hist = _weekly_ecr_db()
    recent_path = PROCESSED / "ecr_weekly_recent.csv"
    if recent_path.exists():
        recent = pd.read_csv(recent_path, parse_dates=["scrape_date"])
        hist = pd.concat([hist[~hist.season.isin(recent.season.unique())], recent], ignore_index=True)
    return hist


def _weekly_ecr_db() -> pd.DataFrame:
    cache = PROCESSED / "ecr_weekly.csv"
    if cache.exists():
        return pd.read_csv(cache, parse_dates=["scrape_date"])
    raw = pd.read_csv(dynastyprocess() / "db_fpecr.csv.gz", low_memory=False,
                      usecols=["page_type", "ecr_type", "id", "player", "team", "ecr", "sd", "scrape_date"])
    raw = raw[raw.ecr_type == "wp"]
    raw = raw[raw.page_type.isin(PAGE_POS)]
    raw["pos"] = raw.page_type.map(PAGE_POS)
    raw["scrape_date"] = pd.to_datetime(raw.scrape_date)
    cal = _week_calendar()
    # assign each scrape to the week whose last game falls on/after the scrape and first game within 7 days
    dates = raw[["scrape_date"]].drop_duplicates()
    rows = []
    for d in dates.scrape_date:
        c = cal[(cal.last_game >= d) & (cal.first_game <= d + pd.Timedelta(days=7))]
        if len(c):
            c = c.sort_values("last_game").iloc[0]
            rows.append((d, int(c.season), int(c.week)))
    wk = pd.DataFrame(rows, columns=["scrape_date", "season", "week"])
    raw = raw.merge(wk, on="scrape_date")
    # keep the latest scrape per (season, week, page)
    raw = raw.sort_values("scrape_date").groupby(["season", "week", "page_type", "id"]).tail(1)
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    fp = ids[ids.fantasypros_id.notna()][["fantasypros_id", "canonical_id"]]
    fp["fantasypros_id"] = fp.fantasypros_id.astype(float).astype(int)
    off = raw[raw.pos != "DEF"].merge(fp, left_on="id", right_on="fantasypros_id", how="inner")
    off["player_id"] = off.canonical_id
    dst = raw[raw.pos == "DEF"].copy()
    nick = dst.player.str.extract(r"(\w+)\s*(?:D/ST|DST|Defense)?$", expand=False)
    dst["player_id"] = "DEF-" + dst.player.map(
        lambda p: next((v for k, v in TEAM_NICK.items() if k in str(p)), None)).astype(str)
    out = pd.concat([off, dst[dst.player_id != "DEF-None"]], ignore_index=True)
    out = out.rename(columns={"sd": "ecr_sd"})[["season", "week", "player_id", "pos", "ecr", "ecr_sd", "scrape_date"]]
    out.to_csv(cache, index=False)
    return out
