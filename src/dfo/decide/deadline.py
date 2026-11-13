"""Trade-deadline posture: push, hold or sell, with the odds behind it, and where the market is.

    PYTHONPATH=src python -m dfo.decide.deadline      # decide_deadline.md / .csv; appends sim_2026_history.csv

Weeks 10-14 (deadline: end of Week 14). Everything from the same numbers as the other modules (P18):
- Posture per team: what one more lineup point is worth to it this season (the simulator's slope from
  the actual standings, dial.csv per_point_2026) against what a later point is worth to every team
  ($0.53). Push when this season is worth at least 1.25x later, build when at most 0.75x, else hold.
- Jordan's needs: for each position, what the best player a building team has there would add to
  his lineup over the rest of the season, and at what salary (Jordan is over the cap, so salary must
  come back within the 25% rule).
- Rentals: players on building teams whose contracts end this season with no designation rights.
  Their owners get nothing for them after Week 18, so they are the cheapest this-season points.
- What Jordan can spend: picks by tier, and bench players who add nothing to his lineup this season.
- What moved since the last run (sim_2026_history.csv).
"""
from __future__ import annotations

import pandas as pd

from dfo.common import PROCESSED

ME = "Jordan"
PUSH, BUILD = 1.25, 0.75


def posture() -> pd.DataFrame:
    from dfo.valuation import conversions
    later = float(conversions.load()["dollars_per_point_later"])
    d = pd.read_csv(PROCESSED / "dial.csv")[["owner", "per_point_2026"]]
    s = pd.read_csv(PROCESSED / "sim_2026.csv")[["owner", "mean_wins", "p_first", "p_top4", "p_playoff_champ", "exp_payout"]]
    out = s.merge(d, on="owner")
    out["now_vs_later"] = (out.per_point_2026 / later).round(2)
    out["posture"] = out.now_vs_later.map(lambda r: "push" if r >= PUSH else "build" if r <= BUILD else "hold")
    return out.sort_values("exp_payout", ascending=False)


def history(post: pd.DataFrame) -> pd.DataFrame:
    """Append this run's odds; return the change since the previous run."""
    f = PROCESSED / "sim_2026_history.csv"
    week = int(pd.read_csv(PROCESSED / "proj_week.csv").week.iloc[0])
    now = post[["owner", "p_first", "p_top4", "p_playoff_champ", "exp_payout"]].assign(
        as_of=pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"), week=week)
    old = pd.read_csv(f) if f.exists() else pd.DataFrame(columns=now.columns)
    prev = old[old.as_of == old.as_of.max()] if len(old) else None
    pd.concat([old, now], ignore_index=True).to_csv(f, index=False)
    if prev is None or not len(prev):
        return pd.DataFrame()
    m = now.merge(prev, on="owner", suffixes=("", "_prev"))
    for c in ("p_first", "p_top4", "p_playoff_champ", "exp_payout"):
        m[f"{c}_change"] = m[c] - m[f"{c}_prev"]
    m["since"] = prev.as_of.iloc[0]
    return m


def market(post: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    from dfo.decide.trade_search import picks_held
    from dfo.decide.trades import live
    st = live()
    p = pd.read_csv(PROCESSED / "prices.csv").set_index("player_id")
    ros = st.pool
    mine = st.rosters[ME]
    base = st.lineup(mine)
    build = set(post[post.posture == "build"].owner)
    rows = []
    for o in build:
        for pid in st.rosters.get(o, set()):
            if pid not in ros.index:
                continue
            gain = st.lineup(mine | {pid}) - base
            if gain < 3:
                continue
            ending = (float(p.salary_2027.get(pid, 0) or 0) == 0) and str(p.rights.get(pid, "none")) in ("none", "nan")
            rows.append(dict(owner=o, player=st.names.get(pid, pid), pos=ros.pos.get(pid), salary_2026=int(st.salary.get(pid, 0) or 0),
                             your_lineup_gain=round(gain, 1), payout_now=round(gain * float(post.set_index("owner").per_point_2026[ME])),
                             rental=ending, later_value_to_him=round(float(p.surplus_2027_29.get(pid, 0) or 0) * st.conv["dollars_per_point_later"])))
    targets = pd.DataFrame(rows).sort_values("your_lineup_gain", ascending=False)
    need = targets.groupby("pos").head(1)[["pos", "player", "owner", "your_lineup_gain", "salary_2026", "rental"]]
    spend = []
    for k in picks_held(ME):
        spend.append(dict(asset=f"{k[0]} round {k[1]}" + (f" ({k[2]}'s)" if k[2] != ME else ""),
                          this_season_loss=0.0, later_value=round(st.pick_points(*k) * st.conv["dollars_per_point_later"])))
    for pid in mine:
        loss = base - st.lineup(mine - {pid})
        if loss <= 2:
            spend.append(dict(asset=str(st.names.get(pid, pid)), this_season_loss=round(loss, 1),
                              later_value=round(float(p.surplus_2027_29.get(pid, 0) or 0) * st.conv["dollars_per_point_later"]),
                              salary_2026=int(st.salary.get(pid, 0) or 0)))
    spend = pd.DataFrame(spend).sort_values("later_value")
    return targets, need, spend


def run() -> str:
    post = posture()
    moved = history(post)
    targets, need, spend = market(post)
    me = post.set_index("owner").loc[ME]
    week = int(pd.read_csv(PROCESSED / "proj_week.csv").week.iloc[0])
    left = max(0, 14 - week + 1)
    L = [f"# Trade deadline: Week {week} ({left} week{'s' if left != 1 else ''} until the Week 14 deadline)", "",
         f"Inputs: projections and simulator as of {pd.Timestamp.now(tz='UTC').date()}; league results through the last "
         "scoreboard Jordan sent. Ideas only; nothing is proposed until Jordan says so.", "",
         f"**Jordan: {me.posture.upper()}.** A lineup point is worth ${me.per_point_2026:.2f} to you this season, "
         f"{me.now_vs_later:.1f}x what it is worth later. 1st {me.p_first:.0%}, top 4 {me.p_top4:.0%}, title {me.p_playoff_champ:.0%}, "
         f"expected payout ${me.exp_payout:.0f}.", ""]
    if len(moved):
        r = moved.set_index("owner").loc[ME]
        L += [f"Since {str(r.since)[:10]}: 1st {r.p_first_change:+.0%}, title {r.p_playoff_champ_change:+.0%}, "
              f"expected payout {r.exp_payout_change:+.0f}.", ""]
    L += ["## Every team's posture", "", "| Team | Posture | This season vs later | Wins | 1st | Top 4 | Title | Expected payout |",
          "|---|---|---|---|---|---|---|---|"]
    for r in post.itertuples():
        L.append(f"| {r.owner} | {r.posture} | {r.now_vs_later:.2f}x | {r.mean_wins:.1f} | {r.p_first:.0%} | {r.p_top4:.0%} | "
                 f"{r.p_playoff_champ:.0%} | ${r.exp_payout:.0f} |")
    buyers = [o for o in post[post.posture == "push"].owner if o != ME]
    L += ["", f"Competing buyers: {', '.join(buyers) or 'none'}. Sellers: {', '.join(post[post.posture == 'build'].owner)}.", "",
          "## Where you would gain most (best player a selling team has at each position)", "",
          "| Pos | Player | Team | Your lineup gain, rest of season | Salary | Rental |", "|---|---|---|---|---|---|"]
    for r in need.itertuples():
        L.append(f"| {r.pos} | {r.player} | {r.owner} | {r.your_lineup_gain:+.0f} | ${r.salary_2026} | {'yes' if r.rental else ''} |")
    rent = targets[targets.rental].head(8)
    L += ["", "## Rentals (contract ends this season, no rights: worth nothing to the seller after Week 18)", ""]
    L += [f"- {r.player} ({r.pos}, {r.owner}, ${r.salary_2026}): {r.your_lineup_gain:+.0f} lineup points, about ${r.payout_now} of payout to you"
          for r in rent.itertuples()] or ["- none on selling teams that would start for you"]
    L += ["", "## What you can spend (no cost to this season)", "",
          "| Asset | This-season loss | Later value |", "|---|---|---|"]
    for r in spend.itertuples():
        L.append(f"| {r.asset} | {r.this_season_loss:.0f} | ${r.later_value:.0f} |")
    ts = PROCESSED / "decide_trade_search.md"
    if ts.exists():
        L += ["", f"Packages with opening, target and walk-away terms: {ts.name} (last built {pd.Timestamp(ts.stat().st_mtime, unit='s').date()})."]
    md = "\n".join(L)
    (PROCESSED / "decide_deadline.md").write_text(md)
    post.to_csv(PROCESSED / "decide_deadline_posture.csv", index=False)
    targets.to_csv(PROCESSED / "decide_deadline_targets.csv", index=False)
    return md


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    print(run())
