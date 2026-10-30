"""Checks on the league-site snapshot tables."""
import csv
from collections import Counter

from dfo.common import PROCESSED


def rows(name):
    return list(csv.DictReader(open(PROCESSED / f"{name}.csv", encoding="utf-8")))


def test_every_rostered_team_maps_to_an_owner_and_fits_roster_limits():
    c = Counter(r["owner"] for r in rows("site_rosters"))
    assert "" not in c and len(c) == 10, c
    assert all(18 <= n <= 22 for n in c.values()), c          # 20 active + up to 2 IR


def test_jordan_roster_matches_week_7_screenshot():
    """Every player on the Week 7 screenshot is still on the roster or has a logged move off it
    (drop or trade away) since; the roster itself changes with every waiver claim."""
    mine = {r["player"] for r in rows("site_rosters") if r["owner"] == "Jordan"}
    moved = {r["player"] for r in rows("site_transactions")
             if r["from_owner"] == "Jordan" and r["type"] in ("drop", "trade") and r["occurred_at"] >= "2026-10-20"}
    week7 = {"Jordan Love", "Chuba Hubbard", "Jahmyr Gibbs", "Ja'Marr Chase", "CeeDee Lamb",
             "Wan'Dale Robinson", "George Kittle", "Bucky Irving", "Will Reichard", "New York Jets D/ST",
             "Brandin Cooks", "Kendrick Bourne", "Parker Washington", "Bryce Young", "Harold Fannin",
             "Zachariah Branch", "Ollie Gordon", "Patrick Mahomes", "Tyrone Tracy", "Darius Cooper",
             "Jaxson Dart", "Chris Brazzell"}
    missing = {p for p in week7 - mine if not any(p in m or m in p for m in moved)}
    assert not missing, missing
