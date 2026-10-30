"""Expected league points over replacement, by season, given the rankings known at an offseason event.

What players at a given FantasyPros rank actually went on to score, by position:
  k = 0 (the coming season)  redraft positional rank
  k = 1-3 seasons later      dynasty positional rank (redraft rank for K/DEF); a season out of the
                             league counts as 0 points
Isotonic fits on log rank. `exclude_season` leaves one season out (as event season and as outcome
season), so backtests never see the result they are scored on.

Replacement level is ex ante: the expected points of the first player past the league's starters
(QB 10, RB 20, WR 30, TE 10, K 10, DEF 10, and 10 FLEX given to whichever of RB/WR/TE scores more).
Value per season is E[max(0, points - replacement)]: a season below replacement is benched or cut,
so value cannot go below zero, and players with the same median but more upside are worth more.
For a full season this expectation comes straight from history: what players with the same
expected points for the coming season actually returned over replacement (used at every horizon) (isotonic fit; above the highest expectation in
history it rises one for one). Outcomes are lopsided (injuries and lost jobs pull seasons down), so
a symmetric spread would overstate upside. For the part of a season still to play, the outcome is
treated as normal around the expected pace with the historical spread scaled to the share left.

Kickers and defenses are streamed. Their replacement is the higher of that level and the streaming
level (18 x weekly points of the best unrostered player by weekly rank, roster depth from the site),
and their value is max(0, E[points] - replacement) with no below-zero floor: their seasons are mostly
noise, so a bad one cannot be spotted early enough to cut (principle P10, docs/principles.md).
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.isotonic import IsotonicRegression

from dfo.common import PROCESSED, load_config

LAST_COMPLETE = 2025
REG_WEEKS = (1, 18)          # the league's regular season is NFL weeks 1-18
SKILL = ["QB", "RB", "WR", "TE"]
HORIZONS = (0, 1, 2, 3)
STREAMED = ["K", "DEF"]


@lru_cache(maxsize=1)
def streaming_level() -> dict[str, float]:
    """Season points a team gets by streaming the best unrostered player each week, K and DEF:
    18 x mean weekly points of players at weekly FantasyPros rank (rostered + 1) to (rostered + 3),
    2020-2025 weeks; rostered = how many the league rosters now (site snapshot)."""
    ev = pd.read_csv(PROCESSED / "proj_weekly_backtest.csv", usecols=["season", "week", "pos", "ecr", "points"])
    ev["wrank"] = ev.groupby(["season", "week", "pos"]).ecr.rank(method="first")
    rostered = pd.read_csv(PROCESSED / "site_rosters.csv").groupby("pos").size()
    out = {}
    for pos in STREAMED:
        n = int(rostered.get(pos, 10))
        g = ev[(ev.pos == pos) & ev.wrank.between(n + 1, n + 3)]
        out[pos] = float(18 * g.points.mean())
    return out


def starters() -> tuple[dict[str, int], list[str], int]:
    lg = load_config("league")
    st = dict(lg["roster"]["starters"])
    flex = st.pop("FLEX", 0)
    return {p: n * lg["league"]["teams"] for p, n in st.items()}, lg["roster"]["flex_eligible"], flex * lg["league"]["teams"]


@lru_cache(maxsize=1)
def season_totals() -> pd.DataFrame:
    """League points and games per player-season, regular season (weeks 1-18)."""
    frames = []
    for s in range(2013, LAST_COMPLETE + 1):
        p = pd.read_csv(PROCESSED / f"points_{s}.csv", low_memory=False)
        p = p[(p.season_type == "REG") & p.week.between(*REG_WEEKS)]
        frames.append(p.groupby(["season", "player_id", "pos"]).points.agg(points="sum", games="size").reset_index())
    return pd.concat(frames, ignore_index=True)


@lru_cache(maxsize=4)
def ranks(event: str) -> pd.DataFrame:
    return pd.read_csv(PROCESSED / f"ranks_at_{event}.csv")


def rank_for(df: pd.DataFrame, k: int) -> pd.Series:
    """The rank used at horizon k: redraft for the coming season, dynasty after (redraft for K/DEF)."""
    if k == 0:
        return df.redraft_rank
    return df.dynasty_rank.where(df.pos.isin(SKILL)).fillna(df.redraft_rank)


def outcomes(event: str, k: int) -> pd.DataFrame:
    r = ranks(event).copy()
    r["rank"] = rank_for(r, k)
    r = r[r["rank"].notna()]
    r["target_season"] = r.season + k
    r = r[r.target_season <= LAST_COMPLETE]
    st = season_totals()
    r = r.merge(st[["season", "player_id", "points", "games"]].rename(columns={"season": "target_season"}),
                on=["target_season", "player_id"], how="left")
    r[["points", "games"]] = r[["points", "games"]].fillna(0)
    return r


def _iso(x, y) -> IsotonicRegression:
    return IsotonicRegression(increasing=False, out_of_bounds="clip").fit(x, y)


class ExpectedValue:
    """Per (position, k): rank -> expected points, games, and expected points over replacement."""

    def __init__(self, event: str = "auction", exclude_season: int | None = None, horizons=HORIZONS,
                 shift: dict[str, float] | None = None):
        """`shift`: season points added to the structural replacement level per position (the
        market-implied level fitted in auction.fit_market); None = structural level."""
        self.event, self.exclude, self.shift = event, exclude_season, shift or {}
        self.points, self.games, self.spread, self.por_pts, self.replacement = {}, {}, {}, {}, {}
        for k in horizons:
            o = outcomes(event, k)
            if exclude_season is not None:
                o = o[(o.season != exclude_season) & (o.target_season != exclude_season)]
            for pos, g in o.groupby("pos"):
                if len(g) >= 40:
                    x = np.log(g["rank"])
                    self.points[(pos, k)] = _iso(x, g.points)
                    self.games[(pos, k)] = _iso(x, g.games)
            if not all((p, k) in self.points for p in ["RB", "WR", "TE"]):
                continue
            # one replacement level for every horizon: the 2029 auction will sell mid-tier production
            # at today's levels; a curve of uncertain far-off expectations would set it too low
            if 0 in self.replacement:
                self.replacement[k] = dict(self.replacement[0])
            else:
                self.replacement[k] = self._replacement(k)
                for pos, d in self.shift.items():
                    if pos in self.replacement[k] and pos not in STREAMED:
                        self.replacement[k][pos] += d
                for pos in STREAMED:
                    if pos in self.replacement[k]:
                        self.replacement[k][pos] = max(self.replacement[k][pos], streaming_level()[pos])
            for pos, g in o.groupby("pos"):
                if (pos, k) in self.points:
                    x = self.points[(pos, k)].predict(np.log(g["rank"]))
                    b = pd.qcut(pd.Series(x).rank(method="first"), 5, labels=False).to_numpy()
                    t = pd.DataFrame(dict(x=x, r=g.points.to_numpy() - x, b=b)).groupby("b").agg(x=("x", "mean"), sd=("r", "std"))
                    a_, b_ = np.linalg.lstsq(np.c_[np.ones(len(t)), t.x], t.sd, rcond=None)[0]
                    self.spread[(pos, k)] = (float(a_), float(b_))
                    if pos in self.replacement[k] and pos not in STREAMED:
                        clipped = (g.points.to_numpy() - self.replacement[k][pos]).clip(min=0)
                        self.por_pts[(pos, k)] = (IsotonicRegression(increasing=True, out_of_bounds="clip")
                                                  .fit(x, clipped), float(x.max()))

    def _replacement(self, k: int) -> dict[str, float]:
        n, flex_pos, flex = starters()
        curve = {p: self.points[(p, k)].predict(np.log(np.arange(1, 200))) for p in n if (p, k) in self.points}
        n = {p: c for p, c in n.items() if p in curve}
        for _ in range(flex):
            best = max(flex_pos, key=lambda p: curve[p][n[p]])
            n[best] += 1
        self.slots = getattr(self, "slots", {})
        self.slots[k] = dict(n)
        return {p: float(curve[p][n[p]]) for p in n}    # rank n+1 (0-based index n)

    def sd(self, pos: str, k: int, x: np.ndarray) -> np.ndarray:
        a, b = self.spread[(pos, k)]
        return np.maximum(a + b * x, 5.0)

    def por_from_points(self, pos: pd.Series, points: pd.Series, k: int, share: pd.Series | float = 1.0) -> pd.Series:
        """E[max(0, points - replacement)] for a season (or the `share` of one still to play) whose
        full-season expected points are `points`. Streamed positions: max(0, E[points] - replacement)."""
        share = pd.Series(share, index=points.index) if np.isscalar(share) else share
        out = pd.Series(0.0, index=points.index)
        for p in pos.dropna().unique():
            m = (pos == p) & points.notna() & (share > 0)
            if not m.any() or k not in self.replacement or p not in self.replacement[k] or (p, k) not in self.spread:
                continue
            x, f = points[m].to_numpy(), share[m].to_numpy()
            mu = f * (x - self.replacement[k][p])
            if p in STREAMED:
                out[m] = np.maximum(mu, 0)
                continue
            s = self.sd(p, k, x) * np.sqrt(f)
            z = mu / s
            normal = mu * norm.cdf(z) + s * norm.pdf(z)
            if (p, 0) in self.por_pts:
                # every horizon uses the coming-season relation (largest sample, best measured); a
                # later season's projection already carries its aging and uncertainty, and the
                # three-years-out relation rests on three seasons of rankings and is noisy at the top
                iso, top = self.por_pts[(p, 0)]
                full = iso.predict(np.minimum(x, top)) + np.maximum(x - top, 0)
                normal = np.where(f >= 1, full, normal)
            out[m] = normal
        return out

    def predict(self, pos: pd.Series, rank: pd.Series, k: int) -> pd.DataFrame:
        out = pd.DataFrame({"points": np.nan, "games": np.nan}, index=rank.index)
        for p in pos.dropna().unique():
            m = (pos == p) & rank.notna()
            if (p, k) in self.points and m.any():
                x = np.log(rank[m])
                out.loc[m, "points"] = self.points[(p, k)].predict(x)
                out.loc[m, "games"] = self.games[(p, k)].predict(x)
        # unranked players: no expected value above replacement
        out["por"] = self.por_from_points(pos, out.points, k).where(out.points.notna(), 0.0)
        return out
