"""History grading and owner models (Phase 6)."""
import pandas as pd

from dfo.common import PROCESSED


def _csv(name):
    return pd.read_csv(PROCESSED / name)


def test_only_real_trades_are_graded():
    a = _csv("hist_trade_assets.csv")
    assert not a.trade_id.str.startswith("T2026-L").any()       # old system's trade ideas are not trades
    assert a.trade_id.nunique() >= 55
    unresolved = a[(a.kind == "player") & a.player_id.isna()]
    assert len(unresolved) <= 8                                   # only conditional terms stay unresolved


def test_trade_grades_are_zero_sum():
    g = _csv("hist_trades.csv")
    s = g.groupby("trade_id")[["points_later", "points_trade_season", "salary_later"]].sum()
    assert (s.abs() < 0.5).all().all()


def test_owner_model_covers_current_owners():
    m = _csv("owner_model.csv")
    owners = set(_csv("site_rosters.csv").owner.dropna())
    assert set(m.owner) == owners and len(m) == 10
    assert m.rfa_match_rate.dropna().between(0, 1).all()
    reads = (PROCESSED / "owner_reads.md").read_text()
    assert all(f"## {o}" in reads for o in owners)
    assert "league average average" not in reads
