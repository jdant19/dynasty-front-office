"""Every graded owner decision, 2014-2025, each judged by the metric that fits it (principle P16).

    PYTHONPATH=src python -m dfo.owners.grades       # writes grades_decisions.csv, grades_by_owner.csv,
                                                     # params_price_curve.csv, owner_season_posture.csv,
                                                     # owner_bridge.csv

Units: lineup points over the waiver level (P15: points for production). Built on the rebuilt weekly
lineups in diagnose.py (lineup_attribution.csv, owner_season_rosters.csv).

Cap decisions (auction buys, RFA wins and matches, tags, waiver/FA keepers): each season the owner held
the player under that decision, the points he added to the owner's starting lineup over the waiver level,
minus what that season's salary normally buys: the points an auction buy at the same price and position
started for its owner in the season bought (price curve, all auctions 2014-2025). Zero = paid the going
rate; positive = more than the money normally buys.
Rookie draft (drafting skill): the player's own points over the waiver level in his first four seasons,
whoever held him, minus the average at that draft slot. Cheapness is not credited here; it shows in the
bridge.
Trades: for each season from the trade season through two seasons after, what each side's received
players added to its lineup net of their salary (no charge for the in-season half: in-season cap is worth
about nothing), minus the same for what it sent, plus picks at the average lineup points a pick in that
round delivered and cap moved at the price curve's slope. Each season is weighted by the owner's
contend/rebuild posture then (payout per lineup point before the season: preseason simulator for
2020-2025, prior-season finish before that), normalized so the average owner-season weighs 1.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from dfo.common import PROCESSED, load_config
from dfo.owners.diagnose import FIRST, LAST, waiver_levels

SKILL = ["QB", "RB", "WR", "TE"]
HAIRCUT = float(load_config("settings")["objective"]["confidence_haircut_per_year"])   # per season after the decision
TRADE_YEARS = 2          # seasons after the trade season counted


def _pos() -> pd.Series:
    p = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").pos.replace({"DST": "DEF"})
    return p[~p.index.duplicated()]


def _names() -> dict[str, str]:
    a = pd.read_csv(PROCESSED / "auctions.csv")
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    m = nm.drop_duplicates("canonical_id", keep="last").set_index("canonical_id").name
    return m.to_dict()


def _load():
    L = pd.read_csv(PROCESSED / "lineup_attribution.csv")
    h = pd.read_csv(PROCESSED / "owner_season_rosters.csv")
    h["pos"] = h.player_id.map(_pos())
    h.loc[h.player_id.astype(str).str.startswith("DEF-"), "pos"] = "DEF"
    nweeks = h.season.map(lambda s: 18 if s >= 2021 else 17)
    h["share"] = (h.w1 - h.w0 + 1) / nweeks
    pts = L.groupby(["season", "owner", "event", "player_id"]).over_waiver.sum()
    h["started_pts"] = [float(pts.get((s, o, e, p), 0.0)) for s, o, e, p in zip(h.season, h.owner, h.event, h.player_id)]
    return L, h


# ------------------------------------------------------------------ price curve
class PriceCurve:
    """Lineup points over waiver an auction buy started for its owner in the season bought, by price
    and position (isotonic, rising with price)."""

    def __init__(self, h: pd.DataFrame):
        b = h[(h.channel == "auction") & (h.w0 == 1) & h.salary.notna()].copy()
        b = b[b.event.str.slice(1, 5).astype(int) == b.season]
        self.n = len(b)
        self.fits, rows = {}, []
        for grp, poss in {"QB": ["QB"], "RB": ["RB"], "WR": ["WR"], "TE": ["TE"], "K/DEF": ["K", "DEF"]}.items():
            g = b[b.pos.isin(poss)]
            iso = IsotonicRegression(increasing=True, out_of_bounds="clip").fit(g.salary, g.started_pts)
            for p in poss:
                self.fits[p] = iso
            for price in (1, 2, 5, 10, 20, 30, 40, 60, 80, 100):
                rows.append(dict(pos=grp, price=price, expected_points=round(float(iso.predict([price])[0]), 1),
                                 buys=len(g)))
        sk = b[b.pos.isin(SKILL)]
        self.slope = float(np.polyfit(sk.salary, sk.started_pts, 1)[0])     # points per extra cap $
        self.table = pd.DataFrame(rows).assign(points_per_dollar=round(self.slope, 3), fit_date=str(dt.date.today()),
                                               seasons=f"{FIRST}-{LAST}")

    def __call__(self, pos, price) -> np.ndarray:
        pos, price = np.asarray(pos, dtype=object), np.asarray(price, dtype=float)
        out = np.zeros(len(pos))
        for p, iso in self.fits.items():
            m = (pos == p) & ~np.isnan(price)
            if m.any():
                out[m] = iso.predict(price[m])
        return out


# ------------------------------------------------------------------ posture
def posture(n: int = 4000) -> pd.DataFrame:
    """Payout per lineup point for each owner-season, known before the season, as a weight (mean 1)."""
    from dfo.sim.future import WEEKLY_SD, beta, payouts, play_seasons   # noqa: F401
    bt = pd.read_csv(PROCESSED / "sim_future_backtest.csv")
    b = beta()
    rows = []
    probe = 10 * 18
    for s, g in bt.groupby("season"):
        owners, st = list(g.owner), g.strength.to_numpy(float)
        sd = np.full(len(st), 0.10 * st.mean())
        base = payouts(play_seasons(st, sd, b, n, np.random.default_rng(11)))
        for j, o in enumerate(owners):
            s2 = st.copy()
            s2[j] += probe
            up = payouts(play_seasons(s2, sd, b, n, np.random.default_rng(11)))
            rows.append(dict(season=s, owner=o, per_point=(up[j] - base[j]) / probe, basis="preseason simulator"))
    sim = pd.DataFrame(rows)
    res = pd.read_csv(PROCESSED / "season_results.csv")
    prior = res.assign(season=res.season + 1).set_index(["season", "owner"]).regular_season_finish
    sim["prior_finish"] = [prior.get((s, o), np.nan) for s, o in zip(sim.season, sim.owner)]
    f = sim.dropna(subset=["prior_finish"])
    iso = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(f.prior_finish, f.per_point)
    early = res[res.season.between(FIRST, 2019)][["season", "owner"]].copy()
    early["prior_finish"] = [prior.get((s, o), np.nan) for s, o in zip(early.season, early.owner)]
    early["per_point"] = np.where(early.prior_finish.notna(), iso.predict(early.prior_finish.fillna(5.5)), sim.per_point.mean())
    early["basis"] = np.where(early.prior_finish.notna(), "prior-season finish", "first season: league average")
    out = pd.concat([early, sim], ignore_index=True)
    out["weight"] = (out.per_point / sim.per_point.mean()).round(3)
    out["per_point"] = out.per_point.round(3)
    mid = midseason(n)
    out = out.merge(mid, on=["season", "owner"], how="left")
    out.to_csv(PROCESSED / "owner_season_posture.csv", index=False)
    return out


def midseason(n: int = 4000) -> pd.DataFrame:
    """Posture at midseason, for in-season trades (archive trades are assumed at midseason): payout per
    lineup point with each team's strength set to its rebuilt first-half lineup and little left unknown.
    Weight normalized to mean 1 across all owner-seasons."""
    from dfo.sim.future import beta, payouts, play_seasons
    L = pd.read_csv(PROCESSED / "lineup_attribution.csv")
    L["mid"] = L.season.map(lambda s: (18 if s >= 2021 else 17) // 2)
    first = L[L.week <= L.mid].groupby(["season", "owner"]).points.sum() / L.groupby("season").mid.first()
    b = beta()
    rows = []
    for s, g in first.groupby(level=0):
        owners = list(g.index.get_level_values(1))
        st = g.to_numpy(float) * 18
        sd = np.full(len(st), 0.04 * st.mean())
        base = payouts(play_seasons(st, sd, b, n, np.random.default_rng(12)))
        for j, o in enumerate(owners):
            s2 = st.copy()
            s2[j] += 180
            up = payouts(play_seasons(s2, sd, b, n, np.random.default_rng(12)))
            rows.append(dict(season=s, owner=o, per_point_mid=(up[j] - base[j]) / 180))
    m = pd.DataFrame(rows)
    m["weight_mid"] = (m.per_point_mid / m.per_point_mid.mean()).round(3)
    m["per_point_mid"] = m.per_point_mid.round(3)
    return m


# ------------------------------------------------------------------ decisions
def cap_decisions(h: pd.DataFrame, curve: PriceCurve) -> pd.DataFrame:
    """Auction buys, RFA wins/matches and waiver/FA keepers: every season held under the decision."""
    x = h[h.channel.isin(["auction", "rfa_won", "rfa_matched", "other"])].copy()
    x["charge"] = curve(x.pos, x.salary) * x.share
    x["acq"] = np.where(x.channel == "other", x.player_id, x.event)
    s0 = np.where(x.channel == "other", x.groupby(["owner", "acq"]).season.transform("min"),
                  pd.to_numeric(x.event.astype(str).str.slice(1, 5), errors="coerce"))
    x["net"] = (x.started_pts - x.charge) * HAIRCUT ** (x.season - s0).clip(lower=0)
    first = x.groupby(["owner", "acq"]).season.transform("min")
    x["year0"] = x.season == first
    rows = []
    for (o, acq, ch), g in x.groupby(["owner", "acq", "channel"]):
        g = g.sort_values("season")
        s0 = int(g.season.iloc[0])
        if ch == "auction":
            s0 = int(str(acq)[1:5])
            if s0 < FIRST:
                continue
        y0 = g[g.season == s0]
        rows.append(dict(type={"auction": "auction", "rfa_won": "rfa_bid", "rfa_matched": "rfa_match",
                               "other": "waiver_fa"}[ch],
                         season=s0, owner=o, player_id=g.player_id.iloc[0], pos=g.pos.iloc[0],
                         price=float(y0.salary.iloc[0]) if len(y0) else float(g.salary.iloc[0]),
                         seasons_held=g.season.nunique(), last_season=int(g.season.max()),
                         points=round(g.started_pts.sum(), 1), expected=round(g.charge.sum(), 1),
                         grade=round(g.net.sum(), 1),
                         grade_year0=round(y0.net.sum(), 1) if len(y0) else np.nan,
                         grade_later=round(g[g.season > s0].net.sum(), 1)))
    return pd.DataFrame(rows)


def rfa_let_go(cap: pd.DataFrame) -> pd.DataFrame:
    """Letting an RFA go: the original owner's grade is minus what the winner's deal returned over price."""
    r = pd.read_csv(PROCESSED / "rfa_bids.csv").groupby(["season", "player"], as_index=False).first()
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    r = r.merge(nm[nm.table == "rfa_bids"][["season", "name", "canonical_id"]].drop_duplicates(["season", "name"]),
                left_on=["season", "player"], right_on=["season", "name"], how="left")
    r = r[(r.outcome == "changed_teams") & r.canonical_id.notna() & (r.season <= LAST)]
    # original owner missing in the RFA sheet for some years: whoever held him at the end of the season before
    h = pd.read_csv(PROCESSED / "owner_season_rosters.csv").sort_values("w1")
    last_holder = h.groupby(["season", "player_id"]).owner.last()
    r["original_owner"] = [o if isinstance(o, str) else last_holder.get((s - 1, pid), np.nan)
                           for o, s, pid in zip(r.original_owner, r.season, r.canonical_id)]
    won = cap[cap.type == "rfa_bid"].set_index(["season", "player_id"])
    rows = []
    for x in r.itertuples():
        if (x.season, x.canonical_id) not in won.index:
            continue
        w = won.loc[(x.season, x.canonical_id)]
        w = w.iloc[0] if isinstance(w, pd.DataFrame) else w
        rows.append(dict(type="rfa_let_go", season=x.season, owner=x.original_owner, player_id=x.canonical_id,
                         pos=w.pos, price=w.price, seasons_held=0, last_season=np.nan, points=np.nan,
                         expected=np.nan, grade=-w.grade, grade_year0=-w.grade_year0, grade_later=-w.grade_later,
                         partner=w.owner))
    return pd.DataFrame(rows)


def tag_decisions(h: pd.DataFrame, curve: PriceCurve) -> pd.DataFrame:
    t = pd.read_csv(PROCESSED / "hist_tags.csv").dropna(subset=["canonical_id"])
    t = t[t.season <= LAST]
    # a tagged player traded before or during the season is graded on whoever started him: the tag set his
    # price; the trade grade covers what was exchanged for him
    got = h.groupby(["season", "player_id"]).started_pts.sum()
    holders = h.groupby(["season", "player_id"]).owner.agg(lambda s: ", ".join(sorted(set(s))))
    rows = []
    for x in t.itertuples():
        pos = "DEF" if str(x.canonical_id).startswith("DEF-") else _POS.get(x.canonical_id)
        exp = float(curve([pos], [x.cost])[0]) if pd.notna(x.cost) else np.nan
        pts = float(got.get((x.season, x.canonical_id), 0.0))
        held = holders.get((x.season, x.canonical_id), "")
        rows.append(dict(type="tag", partner=(held if held and held != x.owner else np.nan), season=x.season, owner=x.owner, player_id=x.canonical_id, pos=pos, price=x.cost,
                         seasons_held=1, last_season=x.season, points=round(pts, 1), expected=round(exp, 1),
                         grade=round(pts - exp, 1), grade_year0=round(pts - exp, 1), grade_later=0.0))
    return pd.DataFrame(rows)


def own_points_over_waiver() -> pd.Series:
    """Each player-season: sum over games of points above his position's weekly waiver level."""
    wl = waiver_levels()
    out = []
    for s in range(FIRST, LAST + 1):
        p = pd.read_csv(PROCESSED / f"points_{s}.csv", low_memory=False,
                        usecols=["season", "season_type", "week", "player_id", "pos", "points"])
        p = p[(p.season_type == "REG") & p.week.between(1, 18)]
        p["pos"] = p.pos.replace({"DST": "DEF"})
        p = p[p.pos.isin(wl)]
        p["ow"] = (p.points - p.pos.map(wl)).clip(lower=0)
        out.append(p.groupby(["season", "player_id"]).ow.sum())
    return pd.concat(out)


def rookie_decisions() -> pd.DataFrame:
    rk = pd.read_csv(PROCESSED / "rookie_drafts.csv")
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    rk = rk.merge(nm[nm.table == "rookie_drafts"][["season", "name", "canonical_id"]].drop_duplicates(["season", "name"]),
                  left_on=["season", "player"], right_on=["season", "name"], how="left")
    rk = rk[rk.season.between(FIRST, LAST)].copy()
    ow = own_points_over_waiver()
    rk["years_observed"] = (LAST - rk.season + 1).clip(upper=4)
    rk["points"] = [sum(float(ow.get((s + k, pid), 0.0)) for k in range(min(4, LAST - s + 1))) if isinstance(pid, str) else 0.0
                    for s, pid in zip(rk.season, rk.canonical_id)]
    full = rk[rk.years_observed == 4]
    iso = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(full.overall, full.points)
    rk["expected"] = iso.predict(rk.overall)
    # partial windows: compare with the slot average over the same number of seasons
    for k in (1, 2, 3):
        f = rk[rk.season <= LAST - k + 1].copy()
        f["pk"] = [sum(float(ow.get((s + j, pid), 0.0)) for j in range(k)) if isinstance(pid, str) else 0.0
                   for s, pid in zip(f.season, f.canonical_id)]
        ik = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(f.overall, f.pk)
        m = rk.years_observed == k
        rk.loc[m, "expected"] = ik.predict(rk.loc[m, "overall"])
    pos = _POS
    return pd.DataFrame(dict(type="rookie_pick", season=rk.season, owner=rk.owner, player_id=rk.canonical_id,
                             player=rk.player, pos=rk.canonical_id.map(pos), price=rk.overall,
                             seasons_held=rk.years_observed, last_season=np.nan, points=rk.points.round(1),
                             expected=rk.expected.round(1), grade=(rk.points - rk.expected).round(1),
                             grade_year0=np.nan, grade_later=np.nan, pick=[f"{s} {r}.{p:02d}" for s, r, p in
                                                                         zip(rk.season, rk["round"], rk.pick)]))


def pick_value(L: pd.DataFrame) -> dict[int, float]:
    """Average lineup points over waiver a rookie pick of each round delivered to its owner over the
    first three seasons (drafts 2014-2023)."""
    d = L[L.channel == "rookie_draft"].copy()
    d["draft"] = d.event.str.slice(1, 5).astype(int)
    d = d[(d.season - d.draft < 3) & (d.draft <= LAST - 2)]
    by = d.groupby("event").over_waiver.sum()
    rk = pd.read_csv(PROCESSED / "rookie_drafts.csv")
    rk = rk[rk.season.between(FIRST, LAST - 2)]
    rk["event"] = [f"D{s}-{o}" for s, o in zip(rk.season, rk.overall)]
    rk["pts"] = rk.event.map(by).fillna(0.0)
    return rk.groupby("round").pts.mean().to_dict()


def trade_decisions(h: pd.DataFrame, L: pd.DataFrame, curve: PriceCurve, post: pd.DataFrame) -> pd.DataFrame:
    a = pd.read_csv(PROCESSED / "hist_trade_assets.csv")
    a = a[a.season <= LAST]
    wt = post.set_index(["season", "owner"]).weight
    wm = post.set_index(["season", "owner"]).weight_mid
    pv = pick_value(L)
    x = h[h.channel == "trade"].copy()
    in_season_part = (x.season == x.event.str.slice(1, 5).astype(int)) & (x.w0 > 1)
    x["charge"] = np.where(in_season_part, 0.0, curve(x.pos, x.salary) * x.share)
    x["net"] = x.started_pts - x.charge
    # each row's posture: midseason for the in-season half of an in-season trade, preseason otherwise
    x["w"] = [float(wm.get((s, o), 1.0)) if ins else float(wt.get((s, o), 1.0))
              for s, o, ins in zip(x.season, x.owner, in_season_part)]
    y0 = x.event.str.slice(1, 5).astype(int)
    x["wnet"] = x.net * x.w * HAIRCUT ** (x.season - y0)
    x["net"] = x.net * HAIRCUT ** (x.season - y0)
    rows = []
    for tid, g in a.groupby("trade_id"):
        y0 = int(g.season.iloc[0])
        teams = sorted(set(g.receiver.dropna()) | set(g.sender.dropna()))
        if len(teams) != 2:
            continue
        side = {}
        for o in teams:
            rec = g[g.receiver == o]
            mine = x[(x.event == tid) & (x.owner == o) & (x.season <= y0 + TRADE_YEARS)]
            by_season = mine.groupby("season").net.sum()
            wby_season = mine.groupby("season").wnet.sum()
            raw_by_season = mine.groupby("season").started_pts.sum()
            picks = rec[rec.kind == "pick"]
            pick_pts = sum(pv.get(int(r), np.nan) * HAIRCUT ** max(0, (yr if pd.notna(yr) else y0 + 1) - y0 + 1)
                           if pd.notna(r) else np.nan for r, yr in zip(picks.pick_round, picks.pick_year))
            unresolved = int(picks.pick_round.isna().sum() + ((rec.kind == "player") & rec.player_id.isna()).sum())
            cap = 0.0
            for r in rec[rec.kind == "cap"].itertuples():
                m = pd.Series([str(r.asset)]).str.extract(r"\$(\d+)\D+(\d{4})").iloc[0]
                amt = float(r.cap) if pd.notna(r.cap) else (float(m[0]) if pd.notna(m[0]) else 0.0)
                yr = int(m[1]) if pd.notna(m[1]) else y0 + 1
                cap += amt * curve.slope * float(wt.get((yr, o), 1.0)) * HAIRCUT ** max(0, yr - y0)
            side[o] = dict(by_season=by_season, wby=wby_season, raw=raw_by_season, picks=len(picks),
                           pick_pts=0.0 if pd.isna(pick_pts) else pick_pts, cap=cap, unresolved=unresolved,
                           players=int((rec.kind == "player").sum()),
                           names="; ".join(str(v) for v in rec.asset.dropna()))
        for o, other in ((teams[0], teams[1]), (teams[1], teams[0])):
            me, them = side[o], side[other]
            seasons = range(y0, min(y0 + TRADE_YEARS, LAST) + 1)
            # what I sent is valued at my posture: the partner's use of it, re-weighted to me
            in_season = bool((x[(x.event == tid)].w0 > 1).any())
            w0 = float(wm.get((y0, o), 1.0)) if in_season else float(wt.get((y0, o), 1.0))
            w = {s: (w0 if s == y0 else float(wt.get((s, o), 1.0))) for s in seasons}
            got = float(me["wby"].sum())
            gave = sum(w[s] * float(them["by_season"].get(s, 0.0)) for s in seasons)   # already discounted
            ts = float(me["wby"].get(y0, 0.0)) - w[y0] * float(them["by_season"].get(y0, 0.0))
            rows.append(dict(type="trade", season=y0, owner=o, partner=other, trade_id=tid,
                             received=me["names"], sent=them["names"],
                             grade=round(got - gave + me["pick_pts"] - them["pick_pts"] + me["cap"] - them["cap"], 1),
                             grade_trade_season=round(ts, 1),
                             grade_unweighted=round(sum(float(me["by_season"].get(s, 0.0)) - float(them["by_season"].get(s, 0.0))
                                                        for s in seasons) + me["pick_pts"] - them["pick_pts"], 1),
                             posture_trade_season=round(w[y0], 2), timing="in-season" if in_season else "offseason", picks_in=me["picks"], picks_out=them["picks"],
                             complete=y0 + TRADE_YEARS <= LAST, unresolved=me["unresolved"] + them["unresolved"]))
    return pd.DataFrame(rows)


_POS = _pos().to_dict()


def build():
    L, h = _load()
    curve = PriceCurve(h)
    curve.table.to_csv(PROCESSED / "params_price_curve.csv", index=False)
    post = posture()
    cap = cap_decisions(h, curve)
    tr = trade_decisions(h, L, curve, post).merge(trade_payouts(h, curve, post), on=["trade_id", "owner"], how="left")
    d = pd.concat([cap, rfa_let_go(cap), tag_decisions(h, curve), rookie_decisions(), tr], ignore_index=True)
    names = _names()
    if "player" not in d:
        d["player"] = np.nan
    d["player"] = d.player.fillna(d.player_id.map(names))
    d = d.sort_values(["season", "type", "owner"]).reset_index(drop=True)
    d.to_csv(PROCESSED / "grades_decisions.csv", index=False)
    return d, curve, post



# ------------------------------------------------------------------ trades in payout dollars
def _payout_table():
    pay = load_config("league")["payouts"]
    reg = {int(k): float(v) for k, v in pay["regular_season"].items()}
    po = {"champion": float(pay["playoffs"][1]), "runner_up": float(pay["playoffs"][2])}
    seeds = int(load_config("league")["playoffs"]["teams"])
    return reg, po, seeds, sum(po.values()) / seeds


def trade_payouts(h: pd.DataFrame, curve: PriceCurve, post: pd.DataFrame) -> pd.DataFrame:
    """Each trade side in payout dollars, from what actually happened (Jordan, 2026-11-05: a title won
    is definite; a pick was only a chance). For each played season from the trade season through two
    after: rebuild both teams' lineups with the trade undone (each player back with the team that sent
    him, so the gain is over the next best option, P16), net of what the salaries moved would otherwise
    buy, plus cap moved; shift each team's
    actual finish by the trade's lineup points times the places a point is worth (P18: 0.011, 2014-2025;
    fractional, payouts interpolated between places), and pay out the counterfactual: regular-season payout at
    the new finish; the actual playoff payout kept if still in the top four, lost if pushed out, and
    the average playoff payout if pushed in. The grade is actual payout minus counterfactual payout.
    Picks are valued at what a pick in that round is expected to deliver (round-average lineup points
    times the average payout per point), discounted 0.88 a year. Traded picks are resolved to the player
    drafted (traded_picks.py, 2026-11-05): one the receiver used counts as that player's lineup points in
    the re-run of every played season of his rookie deal (the grade runs through the last of them),
    plus expectation for rookie seasons not yet played; one passed on counts at what the player
    actually gave whoever drafted him; picks that can't be traced stay at expectation."""
    reg, po, seeds, po_avg = _payout_table()
    o = pd.read_csv(PROCESSED / "owner_season_lineups.csv")
    weeks = o.season.map(lambda s: 18 if s >= 2021 else 17)
    o["total"] = o.lineup_ppg * weeks
    tot = {s: g.set_index("owner").total.to_dict() for s, g in o.groupby("season")}
    # P18: a lineup point over a season moves the finish this many places (2014-2025, every team the
    # same), and later points are worth the league-average value of a point
    from dfo.valuation import conversions
    conv = conversions.load()
    ppp = float(conv["places_per_point"])
    fin = o.set_index(["season", "owner"])[["regular_season_finish", "playoff_result"]]
    per_point = float(conv["dollars_per_point_later"])
    L = pd.read_csv(PROCESSED / "lineup_attribution.csv")
    pv = pick_value(L)
    a = pd.read_csv(PROCESSED / "hist_trade_assets.csv")
    a = a[a.season <= LAST]
    x = h[h.channel == "trade"].copy()
    ins = (x.season == x.event.str.slice(1, 5).astype(int)) & (x.w0 > 1)
    # what the salaries of the players received would otherwise have bought (none for the in-season half)
    x["sal_pts"] = np.where(ins, 0.0, curve(x.pos, x.salary) * x.share)
    sal_cost = x.groupby(["event", "owner", "season"]).sal_pts.sum()
    # the players' part (P16: over the next best option, not over waiver): both teams' lineups rebuilt
    # with the trade undone (each player back with the team that sent him), the rest of the season as it was
    from dfo.owners import diagnose as D
    wk_all = D.weekly(h)
    sender = {}
    for r in a[a.kind == "player"].itertuples():
        if pd.notna(r.player_id):
            sender[(r.trade_id, r.player_id)] = r.sender
    _cache = {}

    def team_total(hs):
        L_ = D.lineups(hs, wk_all[wk_all.season.isin(hs.season.unique())])
        return L_.groupby("owner").points.sum().to_dict()

    def lineup_delta(tid, s, t0, t1):
        hs = h[(h.season == s) & h.owner.isin([t0, t1])]
        if (s, t0, t1) not in _cache:
            _cache[(s, t0, t1)] = team_total(hs)
        base = _cache[(s, t0, t1)]
        cf = hs.copy()
        m = cf.event == tid
        cf.loc[m, "owner"] = [sender.get((tid, p), o) for p, o in zip(cf.loc[m, "player_id"], cf.loc[m, "owner"])]
        alt = team_total(cf)
        return {t: base.get(t, 0.0) - alt.get(t, 0.0) for t in (t0, t1)}
    # traded picks resolved to the player drafted (traded_picks.py): a pick the receiver used counts
    # as that player's lineup points net of what his rookie salary normally buys, in every played
    # season of his rookie deal; one the receiver passed on counts at what the player actually gave
    # whoever drafted him (all played seasons, average payout per point); unknown picks at expectation
    tp = pd.read_csv(PROCESSED / "traded_picks.csv") if (PROCESSED / "traded_picks.csv").exists() else None
    hd = h[h.channel == "rookie_draft"].copy()
    hd["net"] = hd.started_pts - curve(hd.pos, hd.salary) * hd.share
    pick_net = hd.groupby(["event", "owner", "season"]).net.sum()
    pick_total = hd.groupby(["event", "owner"]).net.sum()

    def pay_frac(s, team, x):
        """Payout at a fractional finish: between the payouts of the places either side."""
        ks = np.arange(1, 11)
        return float(np.interp(min(max(x, 1.0), 10.0), ks, [pay(s, team, k) for k in ks]))

    def pay(s, team, finish):
        f = fin.loc[(s, team)]
        actual_in = f.regular_season_finish <= seeds
        out = reg.get(int(finish), 0.0)
        if finish <= seeds:
            out += po.get(f.playoff_result, 0.0) if actual_in else po_avg
        return out

    rows = []
    for tid, g in a.groupby("trade_id"):
        y0 = int(g.season.iloc[0])
        teams = sorted(set(g.receiver.dropna()) | set(g.sender.dropna()))
        if len(teams) != 2:
            continue
        t0, t1 = teams
        caps = {t: {} for t in teams}
        for r in g[g.kind == "cap"].itertuples():
            m = pd.Series([str(r.asset)]).str.extract(r"\$(\d+)\D+(\d{4})").iloc[0]
            amt = float(r.cap) if pd.notna(r.cap) else (float(m[0]) if pd.notna(m[0]) else 0.0)
            yr = int(m[1]) if pd.notna(m[1]) else y0 + 1
            caps[r.receiver][yr] = caps[r.receiver].get(yr, 0.0) + amt * curve.slope
        season_rows = {t: [] for t in teams}
        used = {t: {} for t in teams}           # season -> lineup points from picks this side drafted
        realized = {t: 0.0 for t in teams}      # $ from picks this side passed on, at what they became
        expected_pk = {t: [] for t in teams}    # (round, year) still valued at expectation
        names = {t: [] for t in teams}
        last = y0 + TRADE_YEARS
        mine = tp[tp.trade_id == tid] if tp is not None else g.iloc[0:0]
        for r in mine.itertuples():
            t = r.receiver
            if t not in used:
                continue
            if r.use == "used by receiver" and isinstance(r.event, str):
                for (ev, o, ss), v in pick_net.loc[[r.event]].items() if r.event in pick_net.index.get_level_values(0) else []:
                    if o == t and ss <= LAST:
                        used[t][ss] = used[t].get(ss, 0.0) + float(v)
                        last = max(last, ss)
                names[t].append(f"{r.asset} = {r.player}")
                seen = max(0, min(3, LAST - int(r.pick_year) + 1))
                if seen < 3 and pd.notna(r.round):        # rookie seasons not played yet: expectation
                    realized[t] += (pv.get(int(r.round), 0.0) * (3 - seen) / 3 * per_point
                                    * HAIRCUT ** max(0, int(r.pick_year) - y0 + 1))
            elif r.use == "re-traded" and isinstance(r.event, str):
                realized[t] += float(pick_total.get((r.event, r.drafted_by), 0.0)) * per_point
                names[t].append(f"{r.asset} = {r.player} (drafted by {r.drafted_by})")
            elif pd.notna(r.round):
                expected_pk[t].append((int(r.round), r.pick_year))
                names[t].append(f"{r.asset}" + (f" = {r.player}?" if isinstance(r.player, str) else ""))
        for s in range(y0, min(last, LAST) + 1):
            if s not in tot or t0 not in tot[s] or t1 not in tot[s]:
                continue
            in_win = s <= y0 + TRADE_YEARS
            ld = lineup_delta(tid, s, t0, t1) if in_win else {t0: 0.0, t1: 0.0}
            dd = {}
            for t, u in ((t0, t1), (t1, t0)):
                dd[t] = ((ld[t] - float(sal_cost.get((tid, t, s), 0.0)) + float(sal_cost.get((tid, u, s), 0.0))
                          + caps[t].get(s, 0.0) - caps[u].get(s, 0.0) if in_win else 0.0)
                         + used[t].get(s, 0.0) - used[u].get(s, 0.0))
            for t, d in ((t0, dd[t0]), (t1, dd[t1])):
                f = int(fin.loc[(s, t)].regular_season_finish)
                f_cf = min(max(f + ppp * d, 1.0), 10.0)      # without the trade's d points
                season_rows[t].append((s, d, f, round(f_cf, 1), pay(s, t, f) - pay_frac(s, t, f_cf)))
        for t, other in ((t0, t1), (t1, t0)):
            pk = realized[t] - realized[other]
            for side, sign in ((t, 1), (other, -1)):
                for rnd, yr in expected_pk[side]:
                    yrs = (yr if pd.notna(yr) else y0 + 1) - y0 + 1
                    pk += sign * pv.get(int(rnd), 0.0) * per_point * HAIRCUT ** max(0, yrs)
            sr = season_rows[t]
            rows.append(dict(trade_id=tid, owner=t, payout_grade=round(sum(r[4] for r in sr) + pk),
                             payout_trade_season=round(sr[0][4]) if sr and sr[0][0] == y0 else 0,
                             payout_picks=round(pk), picks_got="; ".join(names[t]), picks_gave="; ".join(names[other]),
                             seasons_through=min(last, LAST),
                             finish_changes="; ".join(f"{s}: {f} vs {fc:.1f} without" for s, d, f, fc, _ in sr if abs(f - fc) >= 0.05),
                             seasons_realized=len(sr)))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    d, curve, post = build()
    print(curve.table[curve.table.price.isin([1, 5, 20, 60])].to_string(index=False))
    print(d.groupby("type").grade.agg(["size", "mean", "median"]).round(1))
    print(post.groupby("basis").weight.describe().round(2))
