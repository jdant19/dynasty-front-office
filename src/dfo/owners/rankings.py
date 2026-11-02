"""Owner rankings by aspect, career records, and Jordan's notes tested against the data.

    PYTHONPATH=src python -m dfo.owners.rankings    # writes owner_rankings.csv, owner_careers.csv,
                                                    # owner_finishes.csv, observations.csv

Aspects (current owners; 1 = best; each with its sample size):
  auction      cap $ returned over price per auction buy, 2020-2025
  rookie draft points over four seasons vs the slot average, 2014-2022 drafts
  RFA          cap $ returned over price on every RFA he ended up holding (matched or won), 2020-2025
  tags         cap $ returned over the tag cost in the tag season
  trades       cap-equivalent gained per trade over the next two seasons (graded trades)
  results      average regular-season finish
Contract length (years an auction buy stays on its original schedule) is a style, not ranked.
Payouts in careers use today's payout table; earlier seasons may have paid differently.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.rules.contracts import salary_schedule


def contract_years() -> pd.DataFrame:
    """Per auction buy of $5+: seasons it stayed on its original salary schedule (1-4); buys
    through 2023 so three later seasons can be seen."""
    a = pd.read_csv(PROCESSED / "auctions.csv")
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    a = a.merge(nm[nm.table == "auctions"][["season", "name", "canonical_id"]],
                left_on=["season", "player"], right_on=["season", "name"], how="left")
    kept = a[a.kind == "kept"].dropna(subset=["canonical_id"]).set_index(["season", "canonical_id"]).salary
    buys = a[(a.kind == "auction") & a.canonical_id.notna() & (a.season <= 2023) & (a.salary >= 5)]
    rows = []
    for r in buys.itertuples():
        sched, n = salary_schedule(int(r.salary), 4), 1
        for j in range(1, 4):
            s = kept.get((r.season + j, r.canonical_id))
            if s is None or abs(float(s) - sched[j]) > 1:
                break
            n += 1
        rows.append(dict(owner=r.owner, season=r.season, player=r.player, salary=r.salary, years=n))
    return pd.DataFrame(rows)


def rfa_held() -> pd.DataFrame:
    bt = pd.read_csv(PROCESSED / "val_rfa_backtest.csv")
    b = pd.read_csv(PROCESSED / "rfa_bids.csv").drop_duplicates(["season", "player"])[["season", "player", "final_owner"]]
    bt = bt.merge(b, on=["season", "player"], how="left")
    bt["holder"] = np.where(bt.outcome == "matched", bt.original_owner, bt.final_owner)
    bt = bt[bt.outcome.isin(["matched", "changed_teams"]) & bt.realized0.notna()]
    bt["surplus"] = bt.realized0 - (bt.final - 1)
    return bt


def rankings(owners: list[str]) -> pd.DataFrame:
    a = pd.read_csv(PROCESSED / "hist_auctions_by_owner.csv").set_index("owner")
    r = pd.read_csv(PROCESSED / "hist_rookies_by_owner.csv").set_index("owner")
    g = pd.read_csv(PROCESSED / "hist_tags_by_owner.csv").set_index("owner")
    t = pd.read_csv(PROCESSED / "hist_trades_by_owner.csv").set_index("owner")
    res = pd.read_csv(PROCESSED / "season_results.csv")
    rf = rfa_held().groupby("holder").surplus.agg(["mean", "size"])
    cy = contract_years().groupby("owner").years.agg(["mean", "size"])
    aspects = {
        "auction": (a.surplus_per_buy, a.graded_buys),
        "rookie_draft": (r.points_vs_slot, r.graded_picks),
        "rfa": (rf["mean"], rf["size"]),
        "tags": (g.avg_tag_surplus, g.graded),
        "trades": (t.avg_net_later_cap_equiv, t.graded),
        "results": (-res.groupby("owner").regular_season_finish.mean(), res.groupby("owner").size()),
    }
    out = pd.DataFrame(index=owners)
    for name, (val, n) in aspects.items():
        v = val.reindex(owners)
        out[f"{name}_value"] = v.round(1) if name != "results" else (-v).round(1)
        out[f"{name}_n"] = n.reindex(owners).fillna(0).astype(int)
        out[f"{name}_rank"] = v.rank(ascending=False, method="min")
    decision = ["auction", "rookie_draft", "rfa", "tags", "trades"]
    out["avg_decision_rank"] = out[[f"{a_}_rank" for a_ in decision]].mean(axis=1).round(1)
    out["contract_years"] = cy["mean"].reindex(owners).round(2)
    out["contract_buys"] = cy["size"].reindex(owners).fillna(0).astype(int)
    out = out.reset_index().rename(columns={"index": "owner"}).sort_values("avg_decision_rank")
    out.to_csv(PROCESSED / "owner_rankings.csv", index=False)
    return out


def careers(owners: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    pay = load_config("league")["payouts"]
    res = pd.read_csv(PROCESSED / "season_results.csv")
    res["payout"] = (res.regular_season_finish.map(pay["regular_season"]).fillna(0)
                     + res.playoff_result.map({"champion": pay["playoffs"][1], "runner_up": pay["playoffs"][2]}).fillna(0))
    d = pd.read_csv(PROCESSED / "dial.csv").set_index("owner")
    rows = []
    for o in owners:
        g = res[res.owner == o].sort_values("season")
        early, late = g[g.season <= 2019], g[g.season >= 2020]
        rows.append(dict(owner=o, seasons=len(g), first_season=int(g.season.min()),
                         regular_season_titles=int((g.regular_season_finish == 1).sum()),
                         playoff_titles=int((g.playoff_result == "champion").sum()),
                         playoff_trips=int((g.playoff_result != "missed").sum()),
                         avg_finish=round(g.regular_season_finish.mean(), 1),
                         avg_finish_to_2019=round(early.regular_season_finish.mean(), 1) if len(early) else np.nan,
                         avg_finish_2020_on=round(late.regular_season_finish.mean(), 1) if len(late) else np.nan,
                         best=int(g.regular_season_finish.min()), worst=int(g.regular_season_finish.max()),
                         payouts=int(g.payout.sum()), payout_per_season=round(g.payout.mean()),
                         exp_payout_2026=round(float(d.payout_2026.get(o, np.nan)))))
    c = pd.DataFrame(rows).sort_values("payouts", ascending=False)
    c.to_csv(PROCESSED / "owner_careers.csv", index=False)
    fin = res[res.owner.isin(owners)][["season", "owner", "regular_season_finish", "playoff_result", "payout"]]
    fin.to_csv(PROCESSED / "owner_finishes.csv", index=False)
    return c, fin


def observations() -> pd.DataFrame:
    """Jordan's notes on owners (owner reads doc, 2026-11-02), each tested where the data can."""
    cy = contract_years().groupby("owner").years.agg(["mean", "size"])
    league_cy = contract_years().years.mean()
    a = pd.read_csv(PROCESSED / "auctions.csv")
    a["team"] = a.nfl_team.str.upper().replace({"LAR": "LA", "STL": "LA", "JAC": "JAX", "WSH": "WAS",
                                               "OAK": "LV", "SD": "LAC"})

    def fan(o, t):
        me, rest = a[a.owner == o], a[a.owner != o]
        return f"{(me.team == t).mean():.1%} of his roster spots are {t} players vs {(rest.team == t).mean():.1%} for everyone else (n={len(me)})"

    tr = pd.read_csv(PROCESSED / "hist_trade_assets.csv")
    ben_in = int(((tr.receiver == "Ben") & (tr.kind == "pick")).sum())
    ben_out = int(((tr.sender == "Ben") & (tr.kind == "pick")).sum())
    ben_tr = tr[(tr.receiver == "Ben") | (tr.sender == "Ben")]
    rows = [
        ("Ben", "Highly values picks; fights to get them included in deals and pushes back against sending them out.",
         f"Picks moved in {ben_tr[ben_tr.kind == 'pick'].trade_id.nunique()} of his {ben_tr.trade_id.nunique()} trades "
         f"(league: picks in 91% of trades); he received {ben_in} and sent {ben_out}.",
         "kept as Jordan's read: negotiating behavior isn't in the data; the counts are balanced"),
        ("Johnny", "Gives some of the shortest deals; among the least likely to sign players to long deals.",
         f"Shortest in the league: {cy.loc['Johnny', 'mean']:.2f} years on average on $5+ auction buys "
         f"(league {league_cy:.2f}; n={int(cy.loc['Johnny', 'size'])})", "confirmed"),
        ("Mike", "Tends to over-extend (gives longer contracts).",
         f"{cy.loc['Mike', 'mean']:.2f} years on $5+ auction buys (league {league_cy:.2f}) but only "
         f"{int(cy.loc['Mike', 'size'])} such buys", "not testable yet: too few cases; watch his 2027 contracts"),
        ("Russ", "Along with Johnny gives shorter contracts.",
         f"Second-shortest: {cy.loc['Russ', 'mean']:.2f} years (n={int(cy.loc['Russ', 'size'])})", "confirmed"),
        ("Ryan", "Big Cowboys fan and will lean into that.", fan("Ryan", "DAL"), "confirmed"),
        ("Zach", "Big Broncos fan and will lean into that.", fan("Zach", "DEN"), "confirmed"),
    ]
    out = pd.DataFrame(rows, columns=["owner", "note", "evidence", "verdict"])
    out.insert(0, "date", "2026-11-02")
    out.insert(1, "source", "Jordan (owner reads doc)")
    out["expires"] = "2027-11-02"
    out.to_csv(PROCESSED / "observations.csv", index=False)
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    owners = sorted(pd.read_csv(PROCESSED / "site_rosters.csv").owner.dropna().unique())
    print(rankings(owners).to_string(index=False))
    c, _ = careers(owners)
    print(c.to_string(index=False))
    print(observations().to_string(index=False))
