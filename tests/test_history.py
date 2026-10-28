"""Integrity checks on the processed history tables."""
import csv
from collections import Counter, defaultdict

from dfo.common import PROCESSED
from dfo.ingest.build_history import STANDINGS_AFTER_W7


def rows(name):
    return list(csv.DictReader(open(PROCESSED / f"{name}.csv", encoding="utf-8")))


def test_every_auction_row_has_an_owner_and_ten_teams_a_season():
    by = defaultdict(set)
    for r in rows("auctions"):
        assert r["owner"], r
        by[int(r["season"])].add(r["owner"])
    for season, owners in by.items():
        assert len(owners) == 10, (season, owners)


def test_auction_seasons_and_kinds():
    k = Counter((int(r["season"]), r["kind"]) for r in rows("auctions"))
    assert k[(2013, "startup_auction")] == 150
    for season in range(2014, 2026):
        assert 45 <= k[(season, "auction")] <= 90, (season, k[(season, "auction")])


def test_rookie_drafts_complete():
    c = Counter(int(r["season"]) for r in rows("rookie_drafts"))
    assert set(c) == set(range(2014, 2027))
    assert all(n == 30 for n in c.values()), c


def test_season_results_one_finish_per_slot_and_one_champion():
    by = defaultdict(list)
    for r in rows("season_results"):
        by[int(r["season"])].append(r)
    assert set(by) == set(range(2013, 2026))
    for season, rs in by.items():
        assert sorted(int(r["regular_season_finish"]) for r in rs) == list(range(1, 11)), season
        assert sum(r["playoff_result"] == "champion" for r in rs) == 1, season


def test_2026_results_reconcile_with_standings_after_week_7():
    rec = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for r in rows("results_2026"):
        if int(r["week"]) > 7 or not r["score_a"]:
            continue
        a, b, sa, sb = r["team_a"], r["team_b"], float(r["score_a"]), float(r["score_b"])
        rec[a][2] += sa; rec[a][3] += sb; rec[b][2] += sb; rec[b][3] += sa
        rec[a if sa > sb else b][0] += 1
        rec[b if sa > sb else a][1] += 1
    for owner, (w, l, pf, pa) in STANDINGS_AFTER_W7.items():
        got = rec[owner]
        assert (got[0], got[1]) == (w, l), (owner, got, (w, l))
        assert abs(got[2] - pf) < 0.02 and abs(got[3] - pa) < 0.02, (owner, got, pf, pa)


def test_schedule_is_double_round_robin():
    pairs = Counter()
    for r in rows("results_2026"):
        pairs[frozenset((r["team_a"], r["team_b"]))] += 1
    assert len(pairs) == 45 and set(pairs.values()) == {2}
