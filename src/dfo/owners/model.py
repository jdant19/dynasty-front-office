"""Owner models: parameters per owner, each with its sample size, and a proposed read for Jordan.

    PYTHONPATH=src python -m dfo.owners.model       # writes data/processed/owner_model.csv, owner_reads.md

Everything comes from graded history (history.py), the 2026 site transactions and the dial. A trait
is only stated in a read when it stands out from the league (at least 1 standard deviation from the
owner average) and rests on enough events; confidence: low under 5 events, medium 5-14, high 15+.
Jordan confirms each read and adds his own notes, which are logged as dated observations (P-level 4).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config

CURRENT = None


def current_owners() -> list[str]:
    return sorted(pd.read_csv(PROCESSED / "site_rosters.csv").owner.dropna().unique())


def table() -> pd.DataFrame:
    owners = current_owners()
    res = pd.read_csv(PROCESSED / "season_results.csv")
    seasons = res.groupby("owner").season.nunique()
    t = pd.read_csv(PROCESSED / "hist_trades_by_owner.csv").set_index("owner")
    a = pd.read_csv(PROCESSED / "hist_auctions_by_owner.csv").set_index("owner")
    r = pd.read_csv(PROCESSED / "hist_rookies_by_owner.csv").set_index("owner")
    f = pd.read_csv(PROCESSED / "hist_rfa_by_owner.csv").set_index("owner")
    g = pd.read_csv(PROCESSED / "hist_tags_by_owner.csv").set_index("owner")
    d = pd.read_csv(PROCESSED / "dial.csv").set_index("owner")
    fs = pd.read_csv(PROCESSED / "sim_future_strength.csv")
    room = fs[fs.season == 2027].set_index("owner").cap_room
    tr = pd.read_csv(PROCESSED / "hist_trades.csv")
    partners = tr.groupby("owner").partner.agg(lambda s: ", ".join(f"{p} {n}" for p, n in s.value_counts().head(2).items()))
    s = pd.read_csv(PROCESSED / "site_transactions.csv")
    adds = s[s.type == "fa_signing"].groupby("to_owner").size()
    drops = s[s.type == "drop"].groupby("from_owner").size()
    from dfo.owners.rankings import contract_years
    cy = contract_years().groupby("owner").years.agg(["mean", "size"])
    results = res.groupby("owner").agg(titles=("playoff_result", lambda x: int((x == "champion").sum())),
                                       avg_finish=("regular_season_finish", "mean"))
    rows = []
    for o in owners:
        g_ = lambda df, c: df[c].get(o, np.nan) if c in df else np.nan
        n_seasons = int(seasons.get(o, 0))
        rows.append(dict(
            owner=o, seasons=n_seasons, titles=int(results.titles.get(o, 0)),
            avg_regular_finish=round(float(results.avg_finish.get(o, np.nan)), 1),
            trades=int(g_(t, "trades") if pd.notna(g_(t, "trades")) else 0),
            trades_per_season=round((g_(t, "trades") if pd.notna(g_(t, "trades")) else 0) / max(n_seasons, 1), 2),
            top_partners=partners.get(o, ""),
            trade_points_now=g_(t, "avg_points_trade_season"), trade_points_later=g_(t, "avg_points_later"),
            trade_net_later_cap_equiv=g_(t, "avg_net_later_cap_equiv"), trades_graded=g_(t, "graded"),
            picks_bought_minus_sold=g_(t, "picks_in_minus_out"),
            auction_spend=g_(a, "avg_spend_per_auction"), auction_price_vs_league=g_(a, "price_vs_league"),
            auction_surplus_per_buy=g_(a, "surplus_per_buy"), auction_graded=g_(a, "graded_buys"),
            auction_age=g_(a, "avg_age_of_buys"),
            spend_QB=g_(a, "spend_share_QB"), spend_RB=g_(a, "spend_share_RB"),
            spend_WR=g_(a, "spend_share_WR"), spend_TE=g_(a, "spend_share_TE"),
            draft_points_vs_slot=g_(r, "points_vs_slot"), draft_graded=g_(r, "graded_picks"),
            draft_reach=g_(r, "reach"), draft_ranked=g_(r, "ranked_picks"),
            draft_RB=g_(r, "share_RB"), draft_WR=g_(r, "share_WR"),
            rfa_bids_per_season=round(g_(f, "players_bid_on") / 8, 1) if pd.notna(g_(f, "players_bid_on")) else np.nan,
            rfa_bid_vs_final=g_(f, "avg_bid_vs_final"), rfa_won_others=g_(f, "won_others"),
            rfa_players_bid_on=g_(f, "players_bid_on"),
            rfa_match_rate=g_(f, "match_rate"), rfa_own=g_(f, "own_rfas"),
            rfa_matched_value_vs_price=g_(f, "matched_value_vs_price"),
            contract_years=round(float(cy["mean"].get(o, np.nan)), 2), contract_buys=int(cy["size"].get(o, 0)),
            tags=g_(g, "tags"), tag_surplus=g_(g, "avg_tag_surplus"), tags_graded=g_(g, "graded"),
            adds_2026=int(adds.get(o, 0)), drops_2026=int(drops.get(o, 0)),
            posture=d.posture.get(o, ""), payout_2026=d.payout_2026.get(o, np.nan),
            payout_per_point_now=d.per_point_2026.get(o, np.nan),
            payout_per_point_2027=d.per_point_2027_haircut.get(o, np.nan),
            cap_room_2027=room.get(o, np.nan)))
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "owner_model.csv", index=False)
    return out


def _conf(n) -> str:
    n = 0 if pd.isna(n) else n
    return "high" if n >= 15 else ("medium" if n >= 5 else "low")


TRAITS = [
    # column, sample-size column (None: not a sample), high text, low text, number format, unit
    ("trades_per_season", "trades", "trades often", "rarely trades", "{:.1f}", "trades a season"),
    ("trade_points_now", "trades_graded", "trades for points now (contender style)",
     "trades current points away for later value", "{:+.0f}", "points in the trade season, per trade"),
    ("trade_net_later_cap_equiv", "trades_graded", "has come out ahead on later seasons in trades",
     "has lost later-season value in trades", "{:+.0f}", "cap-equivalent per trade over the next two seasons"),
    ("picks_bought_minus_sold", "trades", "accumulates picks", "sells picks", "{:+.0f}", "picks bought minus sold"),
    ("auction_spend", "auction_graded", "spends big at the auction", "spends little at the auction",
     "${:.0f}", "per auction"),
    ("auction_price_vs_league", "auction_graded", "pays above the league rate per point",
     "pays below the league rate per point", "{:.2f}x", "the league's price per point"),
    ("auction_surplus_per_buy", "auction_graded", "gets value at the auction", "overpays at the auction",
     "{:+.0f}", "cap $ returned over price per buy (2020-2025)"),
    ("contract_years", "contract_buys", "keeps auction buys on long contracts", "gives short contracts",
     "{:.2f}", "years an auction buy of $5+ stays on its original contract"),
    ("auction_age", "auction_graded", "buys older players", "buys younger players", "{:.1f}", "average age at purchase"),
    ("spend_RB", "auction_graded", "spends heavily on RBs", "spends lightly on RBs", "{:.0%}", "of auction spend on RBs"),
    ("spend_WR", "auction_graded", "spends heavily on WRs", "spends lightly on WRs", "{:.0%}", "of auction spend on WRs"),
    ("draft_points_vs_slot", "draft_graded", "drafts rookies well", "drafts rookies poorly",
     "{:+.0f}", "points over four seasons vs the slot average"),
    ("draft_reach", "draft_ranked", "takes rookies earlier than consensus", "lets rookies fall past consensus",
     "{:+.1f}", "slots vs FantasyPros rookie order (2021+)"),
    ("rfa_bids_per_season", "rfa_players_bid_on", "bids on many RFAs", "bids on few RFAs", "{:.1f}",
     "RFAs bid on a season"),
    ("rfa_bid_vs_final", "rfa_players_bid_on", "bids close to the final RFA price",
     "bids well under the final RFA price", "{:.0%}", "of the final price, average bid"),
    ("rfa_match_rate", "rfa_own", "matches RFA offers", "lets RFAs go", "{:.0%}", "of contested RFAs matched"),
    ("tag_surplus", "tags_graded", "tags well", "tags poorly", "{:+.0f}", "cap $ per tag, value over cost"),
    ("adds_2026", None, "works the waiver wire", "rarely uses waivers", "{:.0f}", "adds this season"),
]


def reads(m: pd.DataFrame) -> str:
    lines = ["# Owner reads (proposed)", "",
             "Built from graded league history (2014-2026) and this season's data. Each trait stands out from the",
             "league average by at least one standard deviation and shows its numbers and sample size. Confirm, correct",
             "or add to each read; your notes are logged as dated observations and scored like any other source.", ""]
    for _, r in m.sort_values("owner").iterrows():
        lines += [f"## {r.owner}", "",
                  f"Now: **{r.posture}** (2026 expected payout ${r.payout_2026:.0f}; a lineup point is worth "
                  f"${r.payout_per_point_now:.2f} now vs ${r.payout_per_point_2027:.2f} in 2027); 2027 cap room ~${r.cap_room_2027:.0f}.",
                  f"Record: {r.seasons} seasons, {r.titles} playoff title{'s' if r.titles != 1 else ''}, average regular-season finish {r.avg_regular_finish}.",
                  f"Trades: {r.trades} ({r.trades_per_season} a season); most with {r.top_partners or 'nobody'}.", ""]
        found = []
        for col, ncol, hi, lo, num, unit in TRAITS:
            v = r[col]
            if pd.isna(v):
                continue
            mu, sd = m[col].mean(), m[col].std()
            if not sd or abs(v - mu) < sd:
                continue
            basis = "this season only" if ncol is None else \
                f"{_conf(r[ncol])} confidence, n={0 if pd.isna(r[ncol]) else int(r[ncol])}"
            found.append(f"- {hi if v > mu else lo}: {num.format(v)} {unit} (league average {num.format(mu)}; {basis})")
        lines += found or ["- nothing stands out from the league average"]
        lines += ["", "Jordan's notes:", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    m = table()
    txt = reads(m)
    (PROCESSED / "owner_reads.md").write_text(txt)
    print(txt)
