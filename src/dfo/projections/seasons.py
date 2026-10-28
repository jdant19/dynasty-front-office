"""Season-level projections: future seasons (dynasty) and rookies, with backtests.

    PYTHONPATH=src python -m dfo.projections.seasons backtest
    PYTHONPATH=src python -m dfo.projections.seasons predict    # 2027-2029 for current players

Future seasons: points per game and games played in seasons y+1, y+2, y+3, from what is known before
season y+1 starts: last two seasons' production and usage, age, experience, draft capital, and the
FantasyPros dynasty positional rank from the last preseason snapshot. Compared against the dynasty
rank alone (turned into points per game by an isotonic fit on prior seasons).

Rookies: points per game in seasons 1-3 from NFL draft round and pick, position, age, and the
FantasyPros rookie rank. Compared against draft capital alone.
"""
from __future__ import annotations

import sys
import warnings

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression

from dfo.common import PROCESSED
from dfo.sources.fetch import CACHE, dynastyprocess

warnings.filterwarnings("ignore")
SKILL = ["QB", "RB", "WR", "TE"]
MIN_GAMES = 4


# ----------------------------------------------------------------------------- data
def season_table() -> pd.DataFrame:
    frames = []
    for s in range(2013, 2027):
        p = pd.read_csv(PROCESSED / f"points_{s}.csv", low_memory=False)
        p = p[(p.season_type == "REG") & p.pos.isin(SKILL)]
        g = p.groupby(["player_id", "season"]).agg(pos=("pos", "last"), games=("points", "size"),
                                                    total=("points", "sum")).reset_index()
        frames.append(g)
    st = pd.concat(frames, ignore_index=True)
    st["ppg"] = st.total / st.games
    xfp = []
    for s in range(2016, 2027):
        f = CACHE / "ffverse" / f"ep_weekly_{s}.csv"
        if f.exists():
            e = pd.read_csv(f, low_memory=False, usecols=["season", "week", "player_id", "total_fantasy_points_exp",
                                                         "rec_attempt", "rec_attempt_team", "rush_attempt",
                                                         "rush_attempt_team"])
            e = e[e.week <= 18]
            xfp.append(e.groupby(["player_id", "season"]).agg(
                xfp_pg=("total_fantasy_points_exp", "mean"),
                tgt_share=("rec_attempt", lambda x: x.sum()),
                team_tgt=("rec_attempt_team", "sum"), car=("rush_attempt", "sum"),
                team_car=("rush_attempt_team", "sum")).reset_index())
    x = pd.concat(xfp, ignore_index=True)
    x["tgt_share"] = x.tgt_share / x.team_tgt.replace(0, np.nan)
    x["car_share"] = x.car / x.team_car.replace(0, np.nan)
    st = st.merge(x[["player_id", "season", "xfp_pg", "tgt_share", "car_share"]], on=["player_id", "season"], how="left")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    info = ids[ids.canonical_id.str.startswith("00-")][["canonical_id", "birthdate", "draft_year", "draft_round",
                                                          "draft_pick", "rookie_season", "fantasypros_id"]]
    st = st.merge(info.rename(columns={"canonical_id": "player_id"}), on="player_id", how="left")
    st["age"] = st.season + 0.67 - pd.to_datetime(st.birthdate, errors="coerce").dt.year \
        - pd.to_datetime(st.birthdate, errors="coerce").dt.dayofyear / 365
    st["exp"] = st.season - pd.to_numeric(st.rookie_season.fillna(st.draft_year), errors="coerce")
    for c in ["draft_round", "draft_pick"]:
        st[c] = pd.to_numeric(st[c], errors="coerce")
    st["draft_pick"] = st.draft_pick.fillna(300)     # undrafted
    st["draft_round"] = st.draft_round.fillna(8)
    return st


def dynasty_ranks() -> pd.DataFrame:
    """Last FantasyPros dynasty positional rank published before each season starts (Sept 1)."""
    d = pd.read_csv(dynastyprocess() / "db_fpecr.csv.gz", low_memory=False,
                    usecols=["ecr_type", "page_type", "id", "pos", "ecr", "scrape_date"])
    d = d[(d.ecr_type == "dp") & d.pos.isin(SKILL)]
    d["scrape_date"] = pd.to_datetime(d.scrape_date)
    d["season"] = d.scrape_date.dt.year + (d.scrape_date.dt.month >= 9).astype(int)   # ranking for next season
    d = d.sort_values("scrape_date").groupby(["season", "id"]).tail(1)
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    fp = ids[ids.fantasypros_id.notna()][["fantasypros_id", "canonical_id"]].copy()
    fp["id"] = fp.fantasypros_id.astype(float).astype(int)
    d = d.merge(fp[["id", "canonical_id"]], on="id")
    return d.rename(columns={"canonical_id": "player_id", "ecr": "dyn_rank"})[["season", "player_id", "dyn_rank"]]


def rookie_ranks() -> pd.DataFrame:
    d = pd.read_csv(dynastyprocess() / "db_fpecr.csv.gz", low_memory=False,
                    usecols=["ecr_type", "id", "pos", "ecr", "scrape_date"])
    d = d[(d.ecr_type == "drk")]
    d["scrape_date"] = pd.to_datetime(d.scrape_date)
    d = d[d.scrape_date.dt.month.between(5, 8)]                    # after the NFL draft, before the season
    d["season"] = d.scrape_date.dt.year
    d = d.sort_values("scrape_date").groupby(["season", "id"]).tail(1)
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    fp = ids[ids.fantasypros_id.notna()][["fantasypros_id", "canonical_id"]].copy()
    fp["id"] = fp.fantasypros_id.astype(float).astype(int)
    d = d.merge(fp[["id", "canonical_id"]], on="id")
    return d.rename(columns={"canonical_id": "player_id", "ecr": "rookie_rank"})[["season", "player_id", "rookie_rank"]]


# ----------------------------------------------------------------------------- future seasons
FEAT = ["pos_code", "ppg", "games", "xfp_pg", "tgt_share", "car_share", "ppg_prev", "games_prev", "age", "exp",
        "draft_round", "draft_pick"]


def transitions(st: pd.DataFrame, k: int) -> pd.DataFrame:
    """Rows: player at end of season y (with y-1 history) -> outcome in season y+k."""
    prev = st[["player_id", "season", "ppg", "games"]].rename(columns={"ppg": "ppg_prev", "games": "games_prev"})
    prev["season"] += 1
    base = st.merge(prev, on=["player_id", "season"], how="left")
    fut = st[["player_id", "season", "ppg", "games"]].rename(columns={"ppg": "ppg_next", "games": "games_next"})
    fut["season"] -= k
    t = base.merge(fut, on=["player_id", "season"], how="left")
    t["target_season"] = t.season + k
    t["games_next"] = t.games_next.fillna(0)
    t["pos_code"] = t.pos.map({p: i for i, p in enumerate(SKILL)})
    return t


def _gbm(loss="squared_error"):
    return HistGradientBoostingRegressor(loss=loss, early_stopping=False, max_iter=250, learning_rate=0.05, min_samples_leaf=30,
                                         l2_regularization=1.0, random_state=7)


def backtest_future(k: int = 1, tests=range(2021, 2026)) -> tuple[pd.DataFrame, pd.DataFrame]:
    st = season_table()
    dyn = dynasty_ranks()
    t = transitions(st, k)
    t = t.merge(dyn.rename(columns={"season": "target_season"}), on=["player_id", "target_season"], how="left")
    t["dyn_log"] = np.log(t.dyn_rank)
    feats = FEAT + ["dyn_log"]
    rows = []
    for s in tests:
        train = t[(t.target_season < s) & (t.games >= MIN_GAMES)]
        test = t[(t.target_season == s) & (t.games >= MIN_GAMES) & t.dyn_rank.notna()].copy()
        played_tr = train[train.games_next >= MIN_GAMES]
        if not len(test):
            continue
        m_ppg = _gbm().fit(played_tr[feats], played_tr.ppg_next)
        m_games = _gbm().fit(train[feats], train.games_next)
        test["model_ppg"] = m_ppg.predict(test[feats])
        test["model_games"] = m_games.predict(test[feats]).clip(0, 17)
        dtr = played_tr[played_tr.dyn_rank.notna()]
        test["rank_ppg"] = np.nan
        for pos in SKILL:
            r, m = dtr[dtr.pos == pos], test.pos == pos
            if len(r) >= 30 and m.any():
                iso = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(np.log(r.dyn_rank), r.ppg_next)
                test.loc[m, "rank_ppg"] = iso.predict(np.log(test.loc[m, "dyn_rank"]))
        test["naive_ppg"] = test.ppg
        for w in (0.25, 0.5):
            test[f"blend{int(w*100)}_ppg"] = np.where(test.rank_ppg.notna(),
                                                     w * test.model_ppg + (1 - w) * test.rank_ppg, test.model_ppg)
        rows.append(test)
    ev = pd.concat(rows)
    played = ev[ev.games_next >= MIN_GAMES]
    summ = []
    for name, g in [("All", played)] + [(p, played[played.pos == p]) for p in SKILL]:
        rec = dict(horizon=f"+{k}", group=name, players=len(g))
        for c in ["naive_ppg", "rank_ppg", "model_ppg", "blend25_ppg", "blend50_ppg"]:
            rec[f"{c.replace('_ppg', '')}_mae"] = round((g[c] - g.ppg_next).abs().mean(), 2)
            rec[f"{c.replace('_ppg', '')}_rank_corr"] = round(np.nanmean(
                [spearmanr(x[c], x.ppg_next).statistic for _, x in g.groupby(["target_season", "pos"]) if len(x) > 8]), 3)
        summ.append(rec)
    return ev, pd.DataFrame(summ)


# ----------------------------------------------------------------------------- rookies
def backtest_rookies(tests=range(2021, 2026)) -> pd.DataFrame:
    st = season_table()
    rk = rookie_ranks()
    first = st[st.exp == 0].copy()
    first = first.merge(rk, on=["player_id", "season"], how="left")
    first["rk_log"] = np.log(first.rookie_rank)
    first["pos_code"] = first.pos.map({p: i for i, p in enumerate(SKILL)})
    first["age"] = first.age.fillna(first.age.median())
    feats_draft = ["pos_code", "draft_round", "draft_pick", "age"]
    feats_all = feats_draft + ["rk_log"]
    out = []
    for s in tests:
        tr = first[(first.season < s) & (first.games >= MIN_GAMES)]
        te = first[(first.season == s) & (first.games >= MIN_GAMES) & first.rookie_rank.notna()].copy()
        if not len(te):
            continue
        te["draft_only"] = _gbm().fit(tr[feats_draft], tr.ppg).predict(te[feats_draft])
        ranked = tr[tr.rookie_rank.notna()]
        te["rank_only"] = np.nan
        for pos in SKILL:
            r, m = ranked[ranked.pos == pos], te.pos == pos
            if len(r) >= 15 and m.any():
                iso = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(r.rk_log, r.ppg)
                te.loc[m, "rank_only"] = iso.predict(te.loc[m, "rk_log"])
        te["model"] = np.where(te.rank_only.notna(), 0.5 * te.draft_only + 0.5 * te.rank_only, te.draft_only)
        out.append(te)
    ev = pd.concat(out)
    rec = dict(group="rookie year 1", players=len(ev))
    ev = ev.dropna(subset=["rank_only"])
    rec["players"] = len(ev)
    for c in ["draft_only", "rank_only", "model"]:
        rec[f"{c}_mae"] = round((ev[c] - ev.ppg).abs().mean(), 2)
        rec[f"{c}_rank_corr"] = round(np.nanmean(
            [spearmanr(x[c], x.ppg).statistic for _, x in ev.groupby(["season", "pos"]) if len(x) > 8]), 3)
    return pd.DataFrame([rec])


# ----------------------------------------------------------------------------- production
MODEL_WEIGHT = {1: 0.25, 2: 0.25, 3: 0.0}   # share of the stats model; the rest is the dynasty rank


def current_dynasty_ranks() -> pd.DataFrame:
    """Latest FantasyPros dynasty positional ranks (DynastyProcess, refreshed weekly)."""
    d = pd.read_csv(dynastyprocess() / "db_fpecr_latest.csv", low_memory=False)
    d = d[(d.ecr_type == "dp") & d.pos.isin(SKILL)]
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    fp = ids[ids.fantasypros_id.notna()][["fantasypros_id", "canonical_id"]].copy()
    fp["id"] = fp.fantasypros_id.astype(float).astype(int)
    d = d.merge(fp[["id", "canonical_id"]], on="id")
    return d.rename(columns={"canonical_id": "player_id", "ecr": "dyn_rank"})[["player_id", "dyn_rank", "scrape_date"]]


GAMES_COLS = ["pos_code", "age", "ppg", "games", "exp", "dyn_log"]


def expected_games(train: pd.DataFrame, base: pd.DataFrame) -> np.ndarray:
    """Expected games k seasons ahead, including seasons lost to injury or leaving the league.
    A small, heavily regularised model on position, age, production, games, experience and dynasty
    rank. Trained on every season, including injury-shortened ones: without the dynasty rank and
    those rows, a starter hurt this year was projected 1.5-4 games short in later years
    (backtest_games, docs/projections.md)."""
    m = HistGradientBoostingRegressor(early_stopping=False, max_iter=150, learning_rate=0.05, max_depth=3, min_samples_leaf=80,
                                      l2_regularization=5.0, random_state=7)
    m.fit(train[GAMES_COLS], train.games_next)
    return np.clip(m.predict(base[GAMES_COLS]) * 17 / 16.5, 0, 17)   # history mixes 16- and 17-game seasons


def backtest_games(tests=range(2021, 2026)) -> pd.DataFrame:
    """Error in expected games, +1 to +3 seasons, for dynasty-ranked players: the earlier version
    (no dynasty rank, full seasons only) against the current one."""
    st = season_table()
    dyn = dynasty_ranks()
    old_cols = [c for c in GAMES_COLS if c != "dyn_log"]
    gbm = lambda: HistGradientBoostingRegressor(early_stopping=False, max_iter=150, learning_rate=0.05, max_depth=3,
                                                min_samples_leaf=80, l2_regularization=5.0, random_state=7)
    rows = []
    for k in (1, 2, 3):
        t = transitions(st, k).merge(dyn.rename(columns={"season": "target_season"}),
                                     on=["player_id", "target_season"], how="left")
        t["dyn_log"] = np.log(t.dyn_rank)
        for s in tests:
            test = t[(t.target_season == s) & t.dyn_rank.notna()].copy()
            old_tr = t[(t.target_season < s) & (t.games >= MIN_GAMES)]
            test["old"] = np.clip(gbm().fit(old_tr[old_cols], old_tr.games_next).predict(test[old_cols]) * 17 / 16.5, 0, 17)
            test["new"] = expected_games(t[t.target_season < s], test)
            test["k"] = k
            rows.append(test)
    ev = pd.concat(rows)
    out = []
    for k, g in ev.groupby("k"):
        for name, x in [("All ranked", g), ("Hurt starters (<8 games, top-60 rank)", g[(g.games < 8) & (g.dyn_rank <= 60)])]:
            out.append(dict(horizon=f"+{k}", group=name, players=len(x),
                            old_mae=round((x.old - x.games_next).abs().mean(), 2),
                            old_bias=round((x.old - x.games_next).mean(), 2),
                            new_mae=round((x.new - x.games_next).abs().mean(), 2),
                            new_bias=round((x.new - x.games_next).mean(), 2)))
    return pd.DataFrame(out)


def predict_future(current_season: int = 2026) -> pd.DataFrame:
    """Expected points per game, games and total for each of the next three seasons, for every
    player with a current dynasty rank or a season this year."""
    st = season_table()
    dyn_hist = dynasty_ranks()
    now = current_dynasty_ranks()
    base = st[st.season == current_season].copy()
    prev = st[st.season == current_season - 1][["player_id", "ppg", "games"]].rename(
        columns={"ppg": "ppg_prev", "games": "games_prev"})
    base = base.merge(prev, on="player_id", how="left")
    base = base.merge(now[["player_id", "dyn_rank"]], on="player_id", how="outer")
    base["pos"] = base.pos.fillna(base.player_id.map(st.drop_duplicates("player_id", keep="last")
                                                     .set_index("player_id").pos))
    base = base[base.pos.isin(SKILL)]
    info = st.drop_duplicates("player_id", keep="last").set_index("player_id")
    for c in ["age", "exp", "draft_round", "draft_pick"]:
        base[c] = base[c].fillna(base.player_id.map(info[c]) + (current_season - base.player_id.map(info.season))
                                 * (1 if c in ("age", "exp") else 0))
    base["pos_code"] = base.pos.map({p: i for i, p in enumerate(SKILL)})
    base["dyn_log"] = np.log(base.dyn_rank)
    # a part season counts as fewer games, scaled to a full season for the games feature
    weeks_done = st[st.season == current_season].games.max()
    base["games"] = (base.games * 17 / max(weeks_done, 1)).clip(upper=17)
    out = base[["player_id", "pos", "dyn_rank", "age"]].copy()
    for k in (1, 2, 3):
        t = transitions(st, k)
        t = t.merge(dyn_hist.rename(columns={"season": "target_season"}), on=["player_id", "target_season"], how="left")
        t["dyn_log"] = np.log(t.dyn_rank)
        feats = FEAT + ["dyn_log"]
        train = t[(t.target_season < current_season) & (t.games >= MIN_GAMES)]   # complete seasons only
        played = train[train.games_next >= MIN_GAMES]
        m_ppg = _gbm().fit(played[feats], played.ppg_next)
        model_ppg = m_ppg.predict(base[feats])
        games = expected_games(t[t.target_season < current_season], base)
        rank_ppg = pd.Series(np.nan, index=base.index)
        dtr = played[played.dyn_rank.notna()]
        for pos in SKILL:
            r, m = dtr[dtr.pos == pos], (base.pos == pos) & base.dyn_rank.notna()
            if len(r) >= 30 and m.any():
                iso = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(np.log(r.dyn_rank), r.ppg_next)
                rank_ppg[m] = iso.predict(np.log(base.loc[m, "dyn_rank"]))
        w = MODEL_WEIGHT[k]
        ppg = np.where(rank_ppg.notna(), w * model_ppg + (1 - w) * rank_ppg, model_ppg)
        yr = current_season + k
        out[f"ppg_{yr}"] = np.round(ppg, 2)
        out[f"games_{yr}"] = np.round(games, 1)
        out[f"points_{yr}"] = np.round(ppg * games, 1)
    out["dyn_rank_as_of"] = str(now.scrape_date.max())
    return out.sort_values(f"points_{current_season + 1}", ascending=False)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    if cmd == "backtest":
        res = []
        for k in (1, 2, 3):
            _, s = backtest_future(k)
            res.append(s)
        fut = pd.concat(res)
        fut.to_csv(PROCESSED / "proj_future_backtest_summary.csv", index=False)
        print(fut.to_string(index=False))
        rk = backtest_rookies()
        rk.to_csv(PROCESSED / "proj_rookie_backtest_summary.csv", index=False)
        print(rk.to_string(index=False))
        gm = backtest_games()
        gm.to_csv(PROCESSED / "proj_games_backtest_summary.csv", index=False)
        print(gm.to_string(index=False))
