"""Player identity checks."""
import csv
from collections import Counter

from dfo.common import PROCESSED


def test_every_rostered_player_has_a_canonical_id():
    ids = {r["site_player_id"]: r["canonical_id"] for r in
           csv.DictReader(open(PROCESSED / "player_ids.csv", encoding="utf-8")) if r["site_player_id"]}
    ros = list(csv.DictReader(open(PROCESSED / "site_rosters.csv", encoding="utf-8")))
    missing = [r["player"] for r in ros if not ids.get(r["player_id"], "").startswith(("00-", "DEF-"))]
    assert not missing, missing


def test_history_names_match_at_least_99_5_percent():
    m = list(csv.DictReader(open(PROCESSED / "name_matches.csv", encoding="utf-8")))
    c = Counter(r["method"] in ("exact", "alias") for r in m)
    assert c[True] / len(m) >= 0.995, c


def test_canonical_ids_are_unique():
    ids = [r["canonical_id"] for r in csv.DictReader(open(PROCESSED / "player_ids.csv", encoding="utf-8"))]
    assert len(ids) == len(set(ids))
