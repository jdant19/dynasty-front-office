"""League scoring computed from nflverse weekly stats, using config/league.yaml.

    PYTHONPATH=src python -m dfo.scoring 2026
writes data/processed/points_<season>.csv: one row per player-week (offense and kickers, keyed by
nflverse gsis id) and per team defense-week (keyed DEF-<team>), regular season and NFL playoffs
(season_type REG / POST; playoff weeks matter for the league's playoff format).
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict

from dfo.common import PROCESSED, load_config
from dfo.sources.fetch import as_of, nflverse


def _f(row: dict, key: str) -> float:
    v = row.get(key)
    try:
        return float(v) if v not in (None, "", "NA") else 0.0
    except ValueError:
        return 0.0


def _fumbles_lost(r: dict) -> float:
    """All fumbles lost, including on kick and punt returns (Yahoo counts those; checked on
    Zachariah Branch, 2026 week 6). Falls back to the offensive components if the total is absent."""
    if r.get("fumbles_lost_total") not in (None, "", "NA"):
        return _f(r, "fumbles_lost_total")
    return _f(r, "sack_fumbles_lost") + _f(r, "rushing_fumbles_lost") + _f(r, "receiving_fumbles_lost")


def offense_points(r: dict, s: dict) -> float:
    k = s["kicker"]
    pts = (
        _f(r, "passing_yards") / s["pass_yds_per_pt"] + _f(r, "passing_tds") * s["pass_td"]
        + _f(r, "passing_interceptions") * s["pass_int"]
        + _f(r, "rushing_yards") / s["rush_yds_per_pt"] + _f(r, "rushing_tds") * s["rush_td"]
        + _f(r, "receptions") * s["reception"]
        + _f(r, "receiving_yards") / s["rec_yds_per_pt"] + _f(r, "receiving_tds") * s["rec_td"]
        + _f(r, "special_teams_tds") * s["return_td"]
        + (_f(r, "passing_2pt_conversions") + _f(r, "rushing_2pt_conversions")
           + _f(r, "receiving_2pt_conversions")) * s["two_pt"]
        + _fumbles_lost(r) * s["fumble_lost"]
        + _f(r, "fumble_recovery_tds") * s["offensive_fumble_return_td"]
        # kicking
        + _f(r, "fg_made_0_19") * k["fg_0_19"] + _f(r, "fg_made_20_29") * k["fg_20_29"]
        + _f(r, "fg_made_30_39") * k["fg_30_39"] + _f(r, "fg_made_40_49") * k["fg_40_49"]
        + (_f(r, "fg_made_50_59") + _f(r, "fg_made_60_")) * k["fg_50_plus"]
        + _f(r, "pat_made") * k["pat_made"]
        + _f(r, "fg_missed") * k["fg_miss"]
    )
    return round(pts, 2)


def points_allowed_score(pa: float, table: dict) -> float:
    for band, val in table.items():
        if band.endswith("+"):
            if pa >= int(band[:-1]):
                return val
        elif "-" in band:
            lo, hi = (int(x) for x in band.split("-"))
            if lo <= pa <= hi:
                return val
        elif pa == int(band):
            return val
    raise ValueError(pa)


def defense_points(t: dict, points_allowed: float, d: dict) -> float:
    blocks = _f(t, "def_punt_blocks") + _f(t, "def_fg_blocks") + _f(t, "def_pat_blocks")
    pts = (
        _f(t, "def_sacks") * d["sack"] + _f(t, "def_interceptions") * d["interception"]
        + _f(t, "fumble_recovery_opp") * d["fumble_recovery"]
        + _f(t, "def_tds") * d["td"] + _f(t, "def_safeties") * d["safety"]
        + blocks * d["blocked_kick"] + _f(t, "special_teams_tds") * d["kick_punt_return_td"]
        + _f(t, "def_2pt_made") * d["extra_point_returned"]
        + points_allowed_score(points_allowed, d["points_allowed"])
    )
    return round(pts, 2)


TEAM_NAMES = {  # nflverse abbreviation -> Yahoo-style defense name
    "ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills", "CAR": "Panthers", "CHI": "Bears",
    "CIN": "Bengals", "CLE": "Browns", "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers",
    "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs", "LA": "Rams", "LAC": "Chargers",
    "LV": "Raiders", "MIA": "Dolphins", "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants",
    "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SEA": "Seahawks", "SF": "49ers", "TB": "Buccaneers",
    "TEN": "Titans", "WAS": "Commanders"}


def compute(season: int) -> list[dict]:
    scoring = load_config("league")["scoring"]
    pfile = nflverse("stats_player", f"stats_player_week_{season}.csv")
    tfile = nflverse("stats_team", f"stats_team_week_{season}.csv")
    gfile = nflverse("schedules", "games.csv")
    stamp = as_of(pfile)
    out = []
    for r in csv.DictReader(open(pfile, encoding="utf-8")):
        if r["season_type"] not in ("REG", "POST"):
            continue
        out.append(dict(season=season, season_type=r["season_type"], week=int(r["week"]), player_id=r["player_id"],
                        player=r["player_display_name"], pos=r["position"], team=r["team"],
                        points=offense_points(r, scoring), source="nflverse stats_player", as_of=stamp))
    allowed = {}
    for g in csv.DictReader(open(gfile, encoding="utf-8")):
        if g["season"] == str(season) and g["home_score"] not in ("", "NA"):
            allowed[(int(g["week"]), g["home_team"])] = float(g["away_score"])
            allowed[(int(g["week"]), g["away_team"])] = float(g["home_score"])
    for t in csv.DictReader(open(tfile, encoding="utf-8")):
        if t["season_type"] not in ("REG", "POST"):
            continue
        wk = int(t["week"])
        pa = allowed.get((wk, t["team"]))
        if pa is None:
            continue
        out.append(dict(season=season, season_type=t["season_type"], week=wk, player_id=f"DEF-{t['team']}",
                        player=TEAM_NAMES.get(t["team"], t["team"]), pos="DEF", team=t["team"],
                        points=defense_points(t, pa, scoring["defense"]),
                        source="nflverse stats_team + schedules", as_of=stamp))
    return out


def main(season: int) -> None:
    rows = compute(season)
    path = PROCESSED / f"points_{season}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    weeks = sorted({r["week"] for r in rows})
    print(f"{len(rows)} player-weeks, weeks {weeks[0]}-{weeks[-1]} -> {path.relative_to(PROCESSED.parents[1])}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026)
