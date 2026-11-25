"""Weekly projection scorecard: each finished week, how our pre-game projections did, so a problem like
the Weeks 9-11 ones (IR players projected 11 points, backup QBs as starters) shows up on its own.

    PYTHONPATH=src python -m dfo.projections.scorecard            # score every frozen, finished week
    PYTHONPATH=src python -m dfo.projections.scorecard backfill   # rebuild frozen weeks from git history

Frozen projections (`proj_week_frozen.csv`): every projection run (`projections.current`) replaces a
player's row for the coming week until his game day (13:00 UTC, before the earliest kickoff), so what
is kept is the last projection made before his game. Weeks before the freezer existed (from Week 9,
when `proj_week.csv` history begins) come from git history the same way.

Scored rows: fantasy-relevant players (rostered in the league, or a FantasyPros rank within QB 24 / RB 48
/ WR 60 / TE 24 / K 16 / DEF 16, or projected 5+), not on bye. Actual: league points (`points_2026.csv`),
0 when he did not play. Per week and position: bias (actual - projected), error (MAE) against FantasyPros'
rank in points, ordering (Spearman within position) against FantasyPros', share inside the 80% range,
below it and above it; and, by injury status, how many played and what they were projected.

Drift flags (last 3 scored weeks, pooled; the 2021-2025 back-test is the reference, docs/projections.md):
inside the 80% range below 70% or above 90%; bias beyond +/-1.0 overall or +/-2.0 at a position with 30+
rows; error 10%+ worse than FantasyPros'; ordering 0.05+ worse; any Out/IR player projected above 1.
"""
from __future__ import annotations

import datetime as dt
import io
import subprocess
import sys

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, ROOT

SEASON = 2026
FROZEN = PROCESSED / "proj_week_frozen.csv"
WEEKLY = PROCESSED / "calibration_weekly.csv"
STATUS = PROCESSED / "calibration_status.csv"
FLAGS = PROCESSED / "calibration_flags.csv"
CAP = {"QB": 24, "RB": 48, "WR": 60, "TE": 24, "K": 16, "DEF": 16}
KEEP = ["season", "week", "player_id", "player", "pos", "team", "owner", "fp_rank", "fp_pts", "proj", "low", "high",
        "bye", "injury_status", "play_prob", "proj_if_plays", "qb_role", "made_at"]
FREEZE_HOUR = 13          # UTC on game day, before the earliest kickoff (London games 13:30)


def _kickoffs(season: int) -> pd.Series:
    from dfo.projections.features import implied_totals
    imp = implied_totals([season])
    t = pd.to_datetime(imp.gameday).dt.tz_localize("UTC") + pd.Timedelta(hours=FREEZE_HOUR)
    return pd.Series(t.to_numpy(), index=pd.MultiIndex.from_arrays([imp.week, imp.team]))


def _merge(old: pd.DataFrame, new: pd.DataFrame, now: pd.Timestamp, kick: pd.Series) -> pd.DataFrame:
    """Replace old rows with new ones for players whose game has not started at `now`."""
    new = new[[c for c in KEEP if c in new.columns]].copy()
    k = pd.Series([kick.get((w, t), pd.NaT) for w, t in zip(new.week, new.team)], index=new.index)
    open_ = k.isna() | (now < k)                                   # bye (no game) or not kicked off yet
    new = new[open_.to_numpy()]
    if old is None or not len(old):
        return new
    key = ["season", "week", "player_id"]
    old = old[~old.set_index(key).index.isin(new.set_index(key).index)]
    return pd.concat([old, new], ignore_index=True)


def freeze(week_rows: pd.DataFrame, now: dt.datetime | None = None) -> pd.DataFrame:
    now = pd.Timestamp(now or dt.datetime.now(dt.timezone.utc))
    old = pd.read_csv(FROZEN) if FROZEN.exists() else None
    out = _merge(old, week_rows, now, _kickoffs(int(week_rows.season.iloc[0])))
    out.sort_values(["season", "week", "pos", "proj"], ascending=[True, True, True, False]).to_csv(FROZEN, index=False)
    return out


def backfill(weeks: list[int] | None = None) -> pd.DataFrame:
    """Rebuild frozen weeks from the git history of proj_week.csv (each commit in time order)."""
    log = subprocess.run(["git", "log", "--reverse", "--format=%H %cI", "--", "data/processed/proj_week.csv"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    kick = _kickoffs(SEASON)
    out = pd.read_csv(FROZEN) if FROZEN.exists() else None
    for line in filter(None, log):
        sha, when = line.split()
        try:
            txt = subprocess.run(["git", "show", f"{sha}:data/processed/proj_week.csv"], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout
            df = pd.read_csv(io.StringIO(txt))
        except Exception:
            continue
        if not len(df) or (weeks and int(df.week.iloc[0]) not in weeks) or int(df.season.iloc[0]) != SEASON:
            continue
        if out is not None and len(out) and int(df.week.iloc[0]) in set(out.week) and weeks is None:
            pass
        out = _merge(out, df, pd.Timestamp(when).tz_convert("UTC"), kick)
    out.sort_values(["season", "week", "pos", "proj"], ascending=[True, True, True, False]).to_csv(FROZEN, index=False)
    return out


def _spearman(a, b) -> float:
    a, b = pd.Series(a).rank(), pd.Series(b).rank()
    return float(a.corr(b)) if len(a) >= 5 else np.nan


def _rows(fz: pd.DataFrame, pts: pd.DataFrame) -> pd.DataFrame:
    fz = fz.copy()
    for c in ("proj_if_plays", "fp_pts", "play_prob"):
        if c not in fz:
            fz[c] = np.nan
    for c in ("injury_status", "qb_role", "owner"):
        fz[c] = fz[c].fillna("") if c in fz else ""
    x = fz[~fz.bye.astype(bool)].merge(pts, on=["season", "week", "player_id"], how="left")
    x["played"] = x.points.notna()
    x["actual"] = x.points.fillna(0.0)
    rel = x.owner.fillna("").ne("") | (x.fp_rank <= x.pos.map(CAP)) | (x.proj.fillna(0) >= 5) | (x.proj_if_plays.fillna(0) >= 5)
    return x[rel]


def score() -> dict:
    if not FROZEN.exists():
        return {}
    fz = pd.read_csv(FROZEN)
    pts = pd.read_csv(PROCESSED / f"points_{SEASON}.csv")
    pts = pts[pts.season_type.eq("REG")][["season", "week", "player_id", "points"]]
    done = set(pts.week)
    # a week is finished once its last game day has passed (Monday's games scored by Tuesday's refresh)
    kick = _kickoffs(SEASON)
    last = pd.Series(kick.to_numpy(), index=kick.index.get_level_values(0)).groupby(level=0).max()
    now = pd.Timestamp(dt.datetime.now(dt.timezone.utc))
    games = fz[~fz.bye.astype(bool)].groupby("week").size()
    # a week the freezer saw only part of (Week 8: history starts after its games) is not scored
    finished = [int(w) for w in sorted(set(fz.week)) if w in done and last.get(w, now) + pd.Timedelta(hours=20) < now
                and games.get(w, 0) >= 300]
    x = _rows(fz[fz.week.isin(finished)], pts)
    rows, srows = [], []
    for (w, pos), g in list(x.groupby(["week", "pos"])) + [((w, "All"), g) for w, g in x.groupby("week")]:
        r = dict(week=int(w), pos=pos, n=len(g), proj=g.proj.mean(), actual=g.actual.mean(),
                 bias=(g.actual - g.proj).mean(), mae=(g.actual - g.proj).abs().mean(),
                 inside=((g.actual >= g.low) & (g.actual <= g.high)).mean(),
                 below=(g.actual < g.low).mean(), above=(g.actual > g.high).mean())
        fp = g[g.fp_pts.notna()] if "fp_pts" in g else g.iloc[0:0]
        r["fp_mae"] = (fp.actual - fp.fp_pts).abs().mean() if len(fp) >= 5 else np.nan
        r["mae_same_rows"] = (fp.actual - fp.proj).abs().mean() if len(fp) >= 5 else np.nan
        rk = g[g.fp_rank.notna()]
        if pos == "All":
            sp = [(_spearman(h.proj, h.actual), _spearman(-h.fp_rank, h.actual), len(h)) for _, h in rk.groupby("pos") if len(h) >= 5]
            wsum = sum(n for *_, n in sp)
            r["order"] = sum(a * n for a, _, n in sp) / wsum if wsum else np.nan
            r["fp_order"] = sum(b * n for _, b, n in sp) / wsum if wsum else np.nan
        else:
            r["order"], r["fp_order"] = _spearman(rk.proj, rk.actual), _spearman(-rk.fp_rank, rk.actual)
        r["ranked_rows"] = len(rk)
        r["projections_made"] = str(g.made_at.max())
        rows.append(r)
    for (w, s), g in x.assign(st=x.injury_status.fillna("").replace("", "none")).groupby(["week", "st"]):
        srows.append(dict(week=int(w), status=s, n=len(g), played=g.played.mean(), proj=g.proj.mean(), actual=g.actual.mean()))
    wkly = pd.DataFrame(rows).round(3)
    st = pd.DataFrame(srows).round(3)
    made = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    wkly.assign(made_at=made).to_csv(WEEKLY, index=False)
    st.assign(made_at=made).to_csv(STATUS, index=False)
    flags = drift(x, finished)
    pd.DataFrame(flags, columns=["check", "value", "limit", "weeks", "note"]).assign(made_at=made).to_csv(FLAGS, index=False)
    return dict(weekly=wkly, status=st, flags=flags)


def drift(x: pd.DataFrame, finished: list[int]) -> list[dict]:
    recent = finished[-3:]
    g = x[x.week.isin(recent)]
    if not len(g):
        return []
    span = f"Weeks {recent[0]}-{recent[-1]}" if len(recent) > 1 else f"Week {recent[0]}"
    out = []
    inside = ((g.actual >= g.low) & (g.actual <= g.high)).mean()
    if not 0.70 <= inside <= 0.90:
        out.append(dict(check="inside 80% range", value=round(inside, 3), limit="0.70-0.90", weeks=span,
                        note="ranges too narrow" if inside < 0.7 else "ranges too wide"))
    b = (g.actual - g.proj).mean()
    if abs(b) > 1.0:
        out.append(dict(check="bias, all", value=round(b, 2), limit="+/-1.0", weeks=span,
                        note="projections too low" if b > 0 else "projections too high"))
    for pos, h in g.groupby("pos"):
        bp = (h.actual - h.proj).mean()
        if len(h) >= 30 and abs(bp) > 2.0:
            out.append(dict(check=f"bias, {pos}", value=round(bp, 2), limit="+/-2.0", weeks=span,
                            note="too low" if bp > 0 else "too high"))
    fp = g[g.fp_pts.notna()] if "fp_pts" in g else g.iloc[0:0]
    if len(fp) >= 50:
        ours, theirs = (fp.actual - fp.proj).abs().mean(), (fp.actual - fp.fp_pts).abs().mean()
        if ours > 1.10 * theirs:
            out.append(dict(check="error vs FantasyPros", value=round(ours, 2), limit=f"<= {1.1 * theirs:.2f}",
                            weeks=span, note=f"FantasyPros {theirs:.2f}"))
    rk = g[g.fp_rank.notna()]
    sp = [(_spearman(h.proj, h.actual), _spearman(-h.fp_rank, h.actual), len(h)) for _, h in rk.groupby(["week", "pos"]) if len(h) >= 5]
    if sp:
        n = sum(k for *_, k in sp)
        ours, theirs = sum(a * k for a, _, k in sp) / n, sum(b * k for _, b, k in sp) / n
        if ours < theirs - 0.05:
            out.append(dict(check="ordering vs FantasyPros", value=round(ours, 3), limit=f">= {theirs - 0.05:.3f}",
                            weeks=span, note=f"FantasyPros {theirs:.3f}"))
    bad = g[g.injury_status.isin(["Out", "IR", "Doubtful"]) & (g.proj > 1)]
    if len(bad):
        out.append(dict(check="Out/IR projected above 1", value=len(bad), limit="0", weeks=span,
                        note=", ".join(bad.sort_values("proj", ascending=False).player.astype(str).head(4))))
    return out


def brief() -> dict:
    """What the Data tab shows: per week (all positions) and per position for the latest week, the
    status table for the latest week, and the drift flags."""
    if not WEEKLY.exists():
        return {}
    w = pd.read_csv(WEEKLY)
    if not len(w):
        return {}
    st = pd.read_csv(STATUS) if STATUS.exists() else pd.DataFrame()
    fl = pd.read_csv(FLAGS) if FLAGS.exists() else pd.DataFrame()
    last = int(w.week.max())
    cols = ["week", "pos", "n", "bias", "mae", "fp_mae", "mae_same_rows", "order", "fp_order", "inside", "below", "above"]
    rec = lambda d: [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in d[cols].to_dict("records")]
    return dict(weeks=rec(w[w.pos == "All"]), latest=rec(w[(w.week == last) & (w.pos != "All")]), latest_week=last,
                status=[{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in st[st.week == last].drop(columns="made_at").to_dict("records")] if len(st) else [],
                flags=[{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in fl.drop(columns="made_at").to_dict("records")] if len(fl) else [],
                made_at=str(w.made_at.iloc[0]),
                source="proj_week_frozen.csv (last projection before each player's game) vs points_2026.csv; "
                       "drift limits from the 2021-2025 back-test (docs/projections.md)")


if __name__ == "__main__":
    if sys.argv[1:2] == ["backfill"]:
        f = backfill()
        print(f.groupby("week").size().to_dict())
    res = score()
    if res:
        print(res["weekly"][res["weekly"].pos == "All"].to_string())
        print(res["status"].to_string())
        print(pd.DataFrame(res["flags"]).to_string() if res["flags"] else "no drift flags")
