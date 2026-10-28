"""League scoring checked against Yahoo's official points and the 2026 matchup totals."""
import csv
from collections import defaultdict

from dfo.common import PROCESSED, RAW
from dfo.scoring import points_allowed_score

PA = {"0": 10, "1-6": 7, "7-13": 4, "14-20": 1, "21-27": 0, "28-34": -1, "35+": -4}


def test_points_allowed_bands():
    assert [points_allowed_score(x, PA) for x in (0, 3, 13, 14, 27, 28, 35, 52)] == [10, 7, 4, 1, 0, -1, -4, -4]


def test_every_transcribed_player_week_matches_yahoo():
    rows = list(csv.DictReader(open(PROCESSED / "scoring_reconciliation.csv", encoding="utf-8")))
    assert len(rows) >= 230
    bad = [r for r in rows if r["status"] not in ("match", "did_not_play")]
    assert not bad, bad


def test_transcribed_starters_sum_to_matchup_scores():
    """Guards the hand transcription: starters must add up to the team's official score."""
    totals = defaultdict(float)
    for r in csv.DictReader(open(RAW / "league" / "yahoo_points_2026_w01-07_transcribed.csv", encoding="utf-8")):
        if r["slot"] != "BN":
            totals[(int(r["week"]), r["owner"])] += float(r["yahoo_points"])
    official = {}
    for r in csv.DictReader(open(PROCESSED / "results_2026.csv", encoding="utf-8")):
        if r["score_a"]:
            official[(int(r["week"]), r["team_a"])] = float(r["score_a"])
            official[(int(r["week"]), r["team_b"])] = float(r["score_b"])
    assert len(totals) == 14
    for key, pts in totals.items():
        assert abs(pts - official[key]) < 0.011, (key, pts, official[key])
