"""Weekly projections: a blended gradient-boosting model with a median and an 80% range.

The range is empirical: the 10th and 90th percentile of what players at the same position with
the same projection actually scored in the 2021-2025 backtest (RangeMap). An earlier version fit
10th/90th-percentile boosting models directly; with a third of training rows at exactly 0 points
those models could stall at 0 and give every player a 0 low end.

    PYTHONPATH=src python -m dfo.projections.weekly backtest   # rolling-origin test, writes report tables
    PYTHONPATH=src python -m dfo.projections.weekly predict    # projections for the next 2026 week

Models compared in the backtest (each trained only on seasons before the test season):
  fp_only  - FantasyPros weekly rank turned into points (isotonic fit on prior seasons, per position)
  usage    - gradient boosting on usage, opportunity, team and matchup features (no FantasyPros)
  blend    - 50/50 average of FantasyPros-rank points and a gradient-boosting model that sees usage,
             matchup and the FantasyPros rank. The 50/50 weight was fixed in advance, not tuned on
             the test seasons (75% model scored marginally better on error; see docs/projections.md).
  fp_proj  - FantasyPros' own projected points, available from 2025 only (comparison)
"""
from __future__ import annotations

import sys
import warnings

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression

from dfo.common import PROCESSED, ROOT
from dfo.projections.features import ECR_FEATURES, FEATURES, build

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
CACHE_FEATURES = ROOT / "data" / "cache" / "features.pkl"
TEST_SEASONS = [2021, 2022, 2023, 2024, 2025]
BLEND_WEIGHT = 0.5   # share of the model in the final blend; the rest is FantasyPros-rank points
RANGE = (0.10, 0.90)   # the "80% range"; fixed in advance, not tuned


def features(refresh: bool = False) -> pd.DataFrame:
    if CACHE_FEATURES.exists() and not refresh:
        return pd.read_pickle(CACHE_FEATURES)
    df = build()
    df.to_pickle(CACHE_FEATURES)
    return df


def _gbm(quantile: float) -> HistGradientBoostingRegressor:
    # early_stopping off: scikit-learn turns it on above 10,000 rows, which stopped some fits at the start
    return HistGradientBoostingRegressor(loss="quantile", quantile=quantile, max_iter=300, early_stopping=False,
                                         learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40,
                                         l2_regularization=1.0, random_state=7)


def _training_rows(train: pd.DataFrame) -> pd.DataFrame:
    t = train[(train.played == 1) | train.ecr.notna()]
    return t[t[["points_std", "points_l3", "prev_ppg", "ecr_log"]].notna().any(axis=1)]


class RangeMap:
    """Projection -> 10th/90th percentile of actual points, per position, from honest (out-of-sample)
    backtest rows. Rows are sorted by projection and cut into bins of at least 150; percentiles are
    interpolated between bin medians and forced to rise with the projection."""

    def __init__(self, ev: pd.DataFrame, proj_col: str = "blend"):
        self.maps = {}
        for pos, g in ev.dropna(subset=[proj_col, "points"]).groupby("pos"):
            g = g.sort_values(proj_col)
            n_bins = max(1, min(25, len(g) // 150))
            chunks = np.array_split(np.arange(len(g)), n_bins)
            x = np.array([g[proj_col].iloc[c].median() for c in chunks])
            lo = np.maximum.accumulate([np.quantile(g.points.iloc[c], RANGE[0]) for c in chunks])
            hi = np.maximum.accumulate([np.quantile(g.points.iloc[c], RANGE[1]) for c in chunks])
            self.maps[pos] = (x, lo, hi)

    @classmethod
    def from_backtest(cls) -> "RangeMap":
        return cls(pd.read_csv(PROCESSED / "proj_weekly_backtest.csv"))

    def predict(self, pos: pd.Series, proj: pd.Series) -> tuple[pd.Series, pd.Series]:
        low = pd.Series(np.nan, index=proj.index)
        high = pd.Series(np.nan, index=proj.index)
        for p, (x, lo, hi) in self.maps.items():
            m = (pos == p) & proj.notna()
            low[m] = np.interp(proj[m], x, lo)
            high[m] = np.interp(proj[m], x, hi)
        return np.minimum(low, proj), np.maximum(high, proj)


class WeeklyModel:
    """FantasyPros-rank mapping + quantile gradient boosting, blended."""

    cols = FEATURES + ECR_FEATURES + ["fp_pts"]

    def __init__(self, train: pd.DataFrame):
        t = _training_rows(train).copy()
        self.iso = {}
        for pos in t.pos.unique():
            r = t[(t.pos == pos) & t.ecr.notna()]
            if len(r) >= 50:
                self.iso[pos] = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(np.log(r.ecr), r.points)
        t["fp_pts"] = self._fp_points_out_of_season(t)
        self.median = _gbm(0.5).fit(t[self.cols], t.points)

    @staticmethod
    def _fp_points_out_of_season(t: pd.DataFrame) -> pd.Series:
        """Rank-to-points for training rows, fit on the other training seasons only, so the model
        learns how much to trust FantasyPros from honest (out-of-sample) values."""
        out = pd.Series(np.nan, index=t.index)
        seasons = sorted(t.loc[t.ecr.notna(), "season"].unique())
        for s in seasons:
            other = t[(t.season != s) & t.ecr.notna()] if len(seasons) > 1 else t[t.ecr.notna()]
            for pos in t.pos.unique():
                r = other[other.pos == pos]
                m = (t.season == s) & (t.pos == pos) & t.ecr.notna()
                if len(r) >= 50 and m.any():
                    iso = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(np.log(r.ecr), r.points)
                    out[m] = iso.predict(np.log(t.loc[m, "ecr"]))
        return out

    def fp_points(self, df: pd.DataFrame) -> pd.Series:
        out = pd.Series(np.nan, index=df.index)
        for pos, iso in self.iso.items():
            m = (df.pos == pos) & df.ecr.notna()
            if m.any():
                out[m] = iso.predict(np.log(df.loc[m, "ecr"]))
        return out

    def predict(self, df: pd.DataFrame, ranges: RangeMap | None = None) -> pd.DataFrame:
        x = df.copy()
        x["fp_pts"] = self.fp_points(x)
        out = pd.DataFrame(index=df.index)
        out["q50"] = self.median.predict(x[self.cols])
        out["fp_pts"] = x.fp_pts
        out["proj"] = np.where(x.fp_pts.notna(), BLEND_WEIGHT * out.q50 + (1 - BLEND_WEIGHT) * x.fp_pts, out.q50)
        if ranges is not None:
            out["low"], out["high"] = ranges.predict(df.pos, out.proj)
        return out


def fit(train: pd.DataFrame, cols: list[str]) -> dict:
    t = _training_rows(train)
    return {q: _gbm(q).fit(t[cols], t.points) for q in (0.1, 0.5, 0.9)}


def predict(models: dict, df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for q, m in models.items():
        out[f"q{int(q * 100)}"] = m.predict(df[cols])
    return out


def fp_only(train: pd.DataFrame, test: pd.DataFrame) -> pd.Series:
    pred = pd.Series(np.nan, index=test.index)
    for pos in test.pos.unique():
        tr = train[(train.pos == pos) & train.ecr.notna()]
        te = test[(test.pos == pos) & test.ecr.notna()]
        if len(tr) < 50 or not len(te):
            continue
        iso = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(np.log(tr.ecr), tr.points)
        pred.loc[te.index] = iso.predict(np.log(te.ecr))
    return pred


def eval_rows(test: pd.DataFrame) -> pd.Series:
    """Ranked player-weeks whose game was still to be played when the rankings were scraped.
    (2025+ rankings are the last version committed before Sunday, so a Sunday game on the scrape
    date counts; Thursday games before it do not.)"""
    gd = pd.to_datetime(test.gameday)
    return test.ecr.notna() & (gd >= pd.to_datetime(test.scrape_date))


def _spearman_by_week(d: pd.DataFrame, col: str) -> float:
    vals = []
    for _, g in d.groupby(["season", "week", "pos"]):
        if len(g) >= 8 and g[col].nunique() > 1:
            vals.append(spearmanr(g[col], g.points).statistic)
    return float(np.nanmean(vals)) if vals else np.nan


def backtest() -> pd.DataFrame:
    df = features()
    rows = []
    for s in TEST_SEASONS:
        train, test = df[df.season < s], df[df.season == s].copy()
        wm = WeeklyModel(train)
        p = wm.predict(test)
        test["fp_only"] = p.fp_pts
        test["usage"] = predict(fit(train, FEATURES), test, FEATURES).q50
        test["blend"] = p.proj
        rows.append(test[eval_rows(test)])
    ev = pd.concat(rows)
    # ranges: each season's range map is built from the other test seasons (leave one season out),
    # and, as a stricter check, from earlier test seasons only (rolling; none for 2021)
    ev["blend_q10"] = ev["blend_q90"] = ev["blend_q10_rolling"] = ev["blend_q90_rolling"] = np.nan
    for s in TEST_SEASONS:
        m = ev.season == s
        lo, hi = RangeMap(ev[ev.season != s]).predict(ev.loc[m, "pos"], ev.loc[m, "blend"])
        ev.loc[m, "blend_q10"], ev.loc[m, "blend_q90"] = lo, hi
        if (ev.season < s).any():
            lo, hi = RangeMap(ev[ev.season < s]).predict(ev.loc[m, "pos"], ev.loc[m, "blend"])
            ev.loc[m, "blend_q10_rolling"], ev.loc[m, "blend_q90_rolling"] = lo, hi
    keep = ["season", "week", "pos", "player_id", "points", "ecr", "fp_only", "usage", "blend", "blend_q10",
            "blend_q90", "blend_q10_rolling", "blend_q90_rolling"] + (["fp_proj"] if "fp_proj" in ev else [])
    ev[keep].to_csv(PROCESSED / "proj_weekly_backtest.csv", index=False)
    return ev


def summarize(ev: pd.DataFrame) -> pd.DataFrame:
    out = []
    for key, g in [("All", ev)] + [(p, ev[ev.pos == p]) for p in ["QB", "RB", "WR", "TE", "K", "DEF"]]:
        g = g.dropna(subset=["fp_only", "blend"])
        rec = dict(group=key, player_weeks=len(g))
        for m in ["fp_only", "usage", "blend"]:
            rec[f"{m}_mae"] = round((g[m] - g.points).abs().mean(), 2)
            rec[f"{m}_rank_corr"] = round(_spearman_by_week(g, m), 3)
        rec["blend_80pct_coverage"] = round(((g.points >= g.blend_q10) & (g.points <= g.blend_q90)).mean(), 3)
        out.append(rec)
    by_season = []
    for s, g in ev.dropna(subset=["fp_only", "blend"]).groupby("season"):
        by_season.append(dict(season=s, player_weeks=len(g),
                              fp_only_mae=round((g.fp_only - g.points).abs().mean(), 2),
                              blend_mae=round((g.blend - g.points).abs().mean(), 2),
                              fp_only_rank_corr=round(_spearman_by_week(g, "fp_only"), 3),
                              blend_rank_corr=round(_spearman_by_week(g, "blend"), 3),
                              coverage=round(((g.points >= g.blend_q10) & (g.points <= g.blend_q90)).mean(), 3),
                              coverage_rolling=round(((g.points >= g.blend_q10_rolling)
                                                      & (g.points <= g.blend_q90_rolling)).mean(), 3)
                              if g.blend_q10_rolling.notna().any() else np.nan))
    g = ev[(ev.season == 2025)].dropna(subset=["fp_proj", "blend"]) if "fp_proj" in ev else ev.iloc[0:0]
    if len(g):
        out.append(dict(group="2025 vs FantasyPros projections", player_weeks=len(g),
                        fp_only_mae=round((g.fp_proj - g.points).abs().mean(), 2),
                        fp_only_rank_corr=round(_spearman_by_week(g, "fp_proj"), 3),
                        blend_mae=round((g.blend - g.points).abs().mean(), 2),
                        blend_rank_corr=round(_spearman_by_week(g, "blend"), 3)))
    summ = pd.DataFrame(out)
    summ.to_csv(PROCESSED / "proj_weekly_backtest_summary.csv", index=False)
    pd.DataFrame(by_season).to_csv(PROCESSED / "proj_weekly_backtest_by_season.csv", index=False)
    return summ


def usage_check_2025() -> dict:
    """2025 has no weekly FantasyPros history in the mirror, so check the usage model against a naive
    season-to-date average on players with at least three games played."""
    df = features()
    train, test = df[df.season < 2025], df[(df.season == 2025) & (df.played == 1) & (df.games_std >= 3)]
    m = fit(train, FEATURES)
    p = predict(m, test, FEATURES)
    return dict(player_weeks=len(test),
                usage_mae=round((p.q50 - test.points).abs().mean(), 2),
                naive_mae=round((test.points_std - test.points).abs().mean(), 2),
                usage_80pct_coverage=round(((test.points >= p.q10) & (test.points <= p.q90)).mean(), 3))


def ros_check(weeks: tuple[int, ...] = (6, 8, 10)) -> pd.DataFrame:
    """Rest of season: at weeks 6, 8 and 10, which per-game rate best predicts a player's per-game
    scoring over the remaining weeks? Compared: season-to-date average, the FantasyPros weekly rank
    mapped to points, our weekly blend, and the rest-of-season mix used in current.py
    (0.5 FantasyPros + 0.25 season average + 0.25 expected points). Players with 4+ games left;
    2021-2025. Note: the mix weights were picked from a few candidates on these same seasons."""
    ev = pd.read_csv(PROCESSED / "proj_weekly_backtest.csv")
    df = features()
    rows = []
    for wk in weeks:
        snap = ev[(ev.week == wk) & ev.pos.isin(["QB", "RB", "WR", "TE"])]
        snap = snap.merge(df[["season", "week", "player_id", "points_std", "xfp_std"]],
                          on=["season", "week", "player_id"], how="left")
        later = (df[(df.week >= wk) & (df.played == 1)].groupby(["season", "player_id"]).points
                 .agg(["mean", "size"]))
        later = later[later["size"] >= 4].rename(columns={"mean": "ros_ppg"}).reset_index()
        t = snap.merge(later, on=["season", "player_id"]).dropna(subset=["points_std", "xfp_std", "fp_only", "blend"])
        t["ros_mix"] = 0.5 * t.fp_only + 0.25 * t.points_std + 0.25 * t.xfp_std
        rec = dict(week=wk, players=len(t))
        for c, name in [("points_std", "season_avg"), ("fp_only", "fp_rank"), ("blend", "weekly_blend"),
                        ("ros_mix", "ros_mix")]:
            rec[f"{name}_mae"] = round((t[c] - t.ros_ppg).abs().mean(), 2)
            rec[f"{name}_rank_corr"] = round(np.nanmean([spearmanr(g[c], g.ros_ppg).statistic
                                                         for _, g in t.groupby(["season", "pos"]) if len(g) > 8]), 3)
        rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "proj_ros_backtest_summary.csv", index=False)
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    if cmd == "backtest":
        ev = backtest()
        print(summarize(ev).to_string(index=False))
        print(pd.read_csv(PROCESSED / "proj_weekly_backtest_by_season.csv").to_string(index=False))
        print("2025 usage check:", usage_check_2025())
        print(ros_check().to_string(index=False))
