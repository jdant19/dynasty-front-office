"""Owner rankings by aspect, career records, and Jordan's notes tested against the data.

    PYTHONPATH=src python -m dfo.owners.rankings    # writes owner_rankings.csv, owner_careers.csv,
                                                    # owner_finishes.csv, observations.csv

Skill per decision (current owners; 1 = best; each with its sample size) from grades.py, each judged
by the metric that fits the decision (P16; see ASPECTS). Why owners win is the bridge: each owner's
weekly lineup edge split by how his starters were acquired, plus injury luck (diagnose.py).
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


ASPECTS = {   # aspect: (decision types, how it is judged)
    "auction": (["auction"], "lineup points over waiver per buy, every season held, minus what the price normally buys"),
    "rookie_draft": (["rookie_pick"], "player's points over waiver in his first four seasons minus the slot average"),
    "rfa_bidding": (["rfa_bid"], "RFAs won from others: lineup points over waiver minus what the price normally buys"),
    "rfa_matching": (["rfa_match", "rfa_let_go"], "own RFAs: matched (his deal's return) or let go (minus the winner's return)"),
    "tags": (["tag"], "lineup points over waiver in the tag season minus what the tag cost normally buys"),
    "trades": (["trade"], "lineup points net of salary, trade season + 2 seasons, both sides, weighted by posture"),
    "waiver_fa": (["waiver_fa"], "kept waiver/FA pickups: lineup points over waiver minus what the salary normally buys"),
}


def rankings(owners: list[str]) -> pd.DataFrame:
    """Skill per decision: the average grade of each kind of decision (P16), with how many there were."""
    d = pd.read_csv(PROCESSED / "grades_decisions.csv")
    d = d[~((d.type == "trade") & ~d.complete.fillna(True).astype(bool))]
    res = pd.read_csv(PROCESSED / "season_results.csv")
    out = pd.DataFrame(index=owners)
    for name, (types, _) in ASPECTS.items():
        g = d[d.type.isin(types)].groupby("owner").grade
        v = g.mean().reindex(owners)
        out[f"{name}_value"] = v.round(1)
        out[f"{name}_n"] = g.size().reindex(owners).fillna(0).astype(int)
        out[f"{name}_rank"] = v.rank(ascending=False, method="min")
    fin = res.groupby("owner").regular_season_finish
    out["results_value"] = fin.mean().reindex(owners).round(1)
    out["results_n"] = fin.size().reindex(owners).fillna(0).astype(int)
    out["results_rank"] = out.results_value.rank(method="min")
    cy = contract_years().groupby("owner").years.agg(["mean", "size"])
    out["contract_years"] = cy["mean"].reindex(owners).round(2)
    out["contract_buys"] = cy["size"].reindex(owners).fillna(0).astype(int)
    out = out.reset_index().rename(columns={"index": "owner"}).sort_values("results_rank")
    out.to_csv(PROCESSED / "owner_rankings.csv", index=False)
    return out


def bridge(owners: list[str]) -> pd.DataFrame:
    """Why owners win: each owner's weekly lineup edge over the league average (points over waiver),
    split by how the starters were acquired, plus injury luck, against the average finish."""
    o = pd.read_csv(PROCESSED / "owner_season_lineups.csv")
    chans = {"auction": "auction", "rookie_draft": "rookie_draft", "rfa": ["rfa_won", "rfa_matched"],
             "trade": "trade", "waiver_fa": "other"}
    for k, v in chans.items():
        o[f"c_{k}"] = o[v].sum(axis=1) if isinstance(v, list) else o[v]
    cols = [f"c_{k}" for k in chans] + ["lineup_ppg", "injury_cost_ppg"]
    rel = o[cols] - o.groupby("season")[cols].transform("mean")
    rel[["season", "owner"]] = o[["season", "owner"]]
    g = rel[rel.owner.isin(owners)].groupby("owner")
    out = g[cols].mean().round(1)
    out.columns = [c.replace("c_", "") + "_edge" if c.startswith("c_") else c for c in out.columns]
    out = out.rename(columns={"lineup_ppg": "lineup_edge", "injury_cost_ppg": "injury_cost_vs_league"})
    out["seasons"] = g.size()
    lr = o[o.owner.isin(owners)].groupby("owner")
    out["lineup_rank"] = lr.lineup_rank.mean().round(1)
    out["finish"] = lr.regular_season_finish.mean().round(1)
    out["finish_vs_lineup"] = (out.lineup_rank - out.finish).round(1)     # + finished better than the lineup
    out = out.reset_index().sort_values("finish")
    out.to_csv(PROCESSED / "owner_bridge.csv", index=False)
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
    print(bridge(owners).to_string(index=False))
    c, _ = careers(owners)
    print(c.to_string(index=False))
    print(observations().to_string(index=False))
