"""Front door data: the brief, open decisions, questions and data health as JSON for the published
page, and the decision journal (append-only).

    PYTHONPATH=src python -m dfo.frontdoor.export      # writes data/frontdoor/*.json, appends data/journal/recommendations.csv

The page (front door artifact) shows these; Jordan's agree/disagree, reasons, answers and notes come
back through the page's store and are saved by dfo.frontdoor.ingest. Brief order (design section 10):
decisions due, posture, our team, market, news, questions, data health. Every item carries the
recommendation, a confidence, the reason and what would change it.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import pandas as pd

from dfo.frontdoor import strict
from dfo.common import PROCESSED, ROOT, load_config

OUT = ROOT / "data" / "frontdoor"
JOURNAL = ROOT / "data" / "journal"
CT = dt.timezone(dt.timedelta(hours=-6))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _read(name: str) -> pd.DataFrame:
    f = PROCESSED / name
    return pd.read_csv(f) if f.exists() else pd.DataFrame()


def _week() -> int:
    return int(_read("proj_week.csv").week.iloc[0])


def waiver_deadline(now: dt.datetime | None = None) -> str:
    """The waiver card's deadline. FAAB closes Tuesday 23:59 PT (config/league.yaml in_season.faab.closes);
    from Wednesday to Monday the week's waivers have run and only $0 adds by waiver priority remain
    (in_season.after_close), each due before the added player's game kicks off."""
    from zoneinfo import ZoneInfo
    local = (now or _now()).astimezone(ZoneInfo("America/Los_Angeles"))
    if local.weekday() == 1:   # Tuesday: the FAAB run is still ahead
        return "Tuesday 11:59pm Pacific"
    return ("waivers ran Tuesday night; now a $0 add by waiver priority, "
            "before the added player's (or dropped player's) game kicks off")


def _age_hours(stamp) -> float | None:
    try:
        t = pd.Timestamp(stamp)
        t = t.tz_localize("UTC") if t.tzinfo is None else t
        return round((pd.Timestamp(_now()) - t).total_seconds() / 3600, 1)
    except Exception:
        return None


def _lu_row(r) -> dict:
    slot = getattr(r, "slot", "")
    return dict(slot=slot if isinstance(slot, str) else "BN", player=r.player, pos=r.pos, team=r.team, opp=r.opp, proj=round(float(r.proj), 1),
                low=round(float(r.low), 1), high=round(float(r.high), 1),
                inj=(r.injury_status if isinstance(getattr(r, "injury_status", None), str) else ""))


def _side(text: str) -> list[dict]:
    """'Ollie Gordon II; 2029 round 1 (Jordan)' -> players with position and salary, and picks."""
    ros = _read("site_rosters.csv")
    by = ros.drop_duplicates("player").set_index("player") if len(ros) else pd.DataFrame()
    out = []
    for item in [x.strip() for x in str(text).split(";") if x.strip()]:
        m = re.match(r"(\d{4}) round (\d)(?: \((\w+)\))?", item)
        if m:
            out.append(dict(kind="pick", name=f"{m.group(1)} round {m.group(2)}" + (f" ({m.group(3)}'s)" if m.group(3) and m.group(3) != "Jordan" else "")))
            continue
        hit = by.index[by.index.str.replace(" D/ST", "").str.replace(r" (Jr\.|Sr\.|II|III)$", "", regex=True)
                       == re.sub(r" (Jr\.|Sr\.|II|III)$", "", item)] if len(by) else []
        r = by.loc[hit[0]] if len(hit) else None
        out.append(dict(kind="player", name=item, pos=(r.pos if r is not None else ""),
                        salary=(int(r.salary_now) if r is not None and pd.notna(r.salary_now) else None)))
    return out


def _trade_detail(r) -> dict:
    pair = lambda t: [x.strip() for x in str(t).split("->")] if "->" in str(t) else [str(t), str(t)]
    years = [dict(season=int(m.group(1)), room=[int(m.group(2)), int(m.group(3))], strength=int(m.group(4)))
             for m in re.finditer(r"(\d{4}): room \$(-?\d+) -> \$(-?\d+), strength ([+-]?\d+)", str(r.your_outlook))]
    pk = re.search(r"picks given ([+-]?\d+) points", str(r.your_outlook))
    terms = []
    for label in ("opening", "target", "walk_away"):
        t = str(getattr(r, label))
        m = re.match(r"[\w-]+: add (.+?) -> you ([+-]\d+), him ([+-]\d+)", t)
        terms.append(dict(label=label.replace("_", "-"), extra=(m.group(1) if m else t), you=(int(m.group(2)) if m else None),
                          him=(int(m.group(3)) if m else None)))
    return dict(type="trade", partner=r.partner, give=_side(r.you_give), get=_side(r.you_get),
                you=dict(now=int(r.jordan_now), later=int(r.jordan_later), total=int(r.jordan)),
                him=dict(now=int(r.partner_now), later=int(r.partner_later), total=int(r.partner_gain)),
                first=pair(r.your_1st), title=pair(r.your_title), years=years,
                picks_given_points=(int(pk.group(1)) if pk else None), terms=terms,
                salary_rule=str(r.salary_rule), forced_drops=(str(r.forced_drops) if isinstance(r.forced_drops, str) else ""),
                trades_per_season=float(r.partner_trades_per_season), message=str(r.draft_message))


def decisions() -> list[dict]:
    wk = _week()
    out = []
    lu = _read(f"decide_lineup_w{wk}.csv")
    md = PROCESSED / f"decide_lineup_w{wk}.md"
    if len(lu):
        start = lu[lu.slot.notna() & (lu.slot != "BN")] if "slot" in lu else lu
        text = md.read_text() if md.exists() else ""
        wp = re.search(r"Win probability (\d+)%[^\n]*vs (\w+)", text)
        news = re.findall(r"Check the news first: ([^\n]+)", text)
        out.append(dict(id=f"lineup-{wk}", kind="Start/sit", deadline="first kickoff of each game window",
                        title=f"Week {wk} lineup" + (f" vs {wp.group(2)}" if wp else ""),
                        recommendation=", ".join(f"{r.slot} {r.player}" for r in start.itertuples()),
                        value=f"win probability {wp.group(1)}%" if wp else "", confidence="medium",
                        reason="Highest win probability from this week's projections and 80% ranges.",
                        change="An injury or inactive report, or a rank jump flagged below." + (f" Flagged: {news[0]}" if news else ""),
                        made_at=str(lu.made_at.iloc[0]) if "made_at" in lu else "",
                        headline=(f"Win chance {wp.group(1)}%" if wp else "Lineup set"),
                        detail=dict(type="lineup", starters=[_lu_row(r) for r in start.itertuples()],
                                    bench=[_lu_row(r) for r in lu[lu.slot.isna() | (lu.slot == "BN")].sort_values("proj", ascending=False).head(5).itertuples()]
                                    if "slot" in lu else [])))
    wv = _read(f"decide_waivers_w{wk}.csv")
    if not len(wv):
        wv = _read(f"decide_waivers_w{wk - 1}.csv")
    room = None
    try:
        from dfo.rules.cap import cap_room
        room = int(cap_room().set_index("owner").room.get("Jordan"))
    except Exception:
        pass
    if len(wv):
        top = wv[wv.net_payout >= 0.25].head(3)
        bid = "$0 (over the cap)" if room is not None and room <= 0 else "$1 if worth $1+, else $0"
        out.append(dict(id=f"waivers-{wk}", kind="Waivers", deadline=waiver_deadline(),
                        title="Waiver claims", recommendation="; ".join(
                            f"add {r.add_player}, drop {r.drop_player}" for r in top.itertuples()) or "no claim worth making",
                        value="; ".join(f"+{r.lineup_points:.1f} pts, ${r.net_payout:.2f}" for r in top.itertuples()),
                        confidence="medium" if not _ranks_missing() else "low (no ranks yet)",
                        reason=f"Rest-of-season lineup gain priced at today's odds; kickers and defenses count for the coming week only (streamed after). Bids {bid}."
                               + (" This week's FantasyPros ranks are not out yet: defense and kicker projections are weak without them, so check the betting line and consensus ranks before choosing between close options." if _ranks_missing() else ""),
                        change="A player's role changing, or a better free agent after the weekend.", made_at=str(wv.rosters_as_of.iloc[0]),
                        headline=(f"Add {top.iloc[0].add_player}, drop {top.iloc[0].drop_player}" if len(top) else "No claim worth making"),
                        detail=dict(type="waivers", bid=bid, rows=[dict(add=r.add_player, add_pos=r.add_pos, drop=r.drop_player, drop_pos=r.drop_pos,
                                                                          pts=round(float(r.lineup_points), 1), net=round(float(r.net_payout), 2))
                                                                     for r in wv[wv.net_payout >= 0.25].head(6).itertuples()])))
    ts = _read("decide_trade_search.csv")
    if len(ts) and wk <= 14:
        good = ts[ts.works_for_both] if "works_for_both" in ts else ts
        for r in good.head(3).itertuples():
            age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(r.inputs)).days
            terms = list(dict.fromkeys([str(r.opening), str(r.target), str(r.walk_away)]))   # identical terms shown once
            stale = f" STALE: built from {str(r.inputs)[:10]} projections ({age} days old); re-run before acting." if age >= 3 else ""
            out.append(dict(id=f"trade-{r.partner}-{re.sub(r'[^a-z]', '', str(r.you_get).lower())[:20]}", kind="Trade idea",
                            deadline="end of Week 14", title=f"{r.partner}: {r.you_get}",
                            recommendation=f"Offer {r.you_give} for {r.you_get}. " + "; ".join(terms),
                            value=(f"you {r.jordan:+.0f} (this season {r.jordan_now:+.0f}, later {r.jordan_later:+.0f}), "
                                   f"him {r.partner_gain:+.0f}; your 1st place {r.your_1st}, title {r.your_title}"),
                            confidence="low" if not stale else "low (stale inputs)",
                            reason=(f"Payout $ (P18). 'This season' adds the regular season and the league playoffs: "
                                    f"a gain with 1st-place odds falling comes from the playoffs alone.{stale}"),
                            change="His posture (a seller turning buyer), injuries, or a counter that moves value.",
                            made_at=str(r.inputs), stale=bool(stale),
                            headline=f"You +${r.jordan:.0f} · title {str(r.your_title).replace(' -> ', '→')}",
                            detail=_trade_detail(r)))
    ad = _read("decide_playoffs_adds.csv")
    if len(ad):
        a = ad[~ad.on_playoff_rival].head(2)
        if len(a):
            out.append(dict(id=f"playoffs-{wk}", kind="Playoff prep", deadline="end of Week 18 (trades: Week 14)",
                            title="Worth adding for the league playoffs",
                            recommendation="; ".join(f"{r.player} ({r.owner})" for r in a.itertuples()),
                            value="; ".join(f"title {r.title_change:+.0%}, ${r.payout_change:+.0f}" for r in a.itertuples()),
                            confidence="low", reason="Keeps are free; owning a player also frees your first draft pick.",
                            change="NFL playoff odds moving, or the seeds changing.",
                            headline=f"{a.iloc[0].player}: title {a.iloc[0].title_change:+.0%}",
                            detail=dict(type="playoffs", rows=[dict(player=r.player, pos=r.pos, nfl=r.nfl, owner=r.owner,
                                                                     rival=bool(r.on_playoff_rival), games=round(float(r.exp_games), 1),
                                                                     title=round(float(r.title_change), 3), payout=int(round(r.payout_change)))
                                                                for r in ad.head(8).itertuples()])))
    off = _read("decide_offseason_2027.csv")
    if len(off):
        b = off.iloc[0]
        out.append(dict(id="offseason-2027-plan", kind="Offseason plan", deadline="2027 designation day",
                        title="2027 designations", recommendation=str(b.scenario),
                        value=f"${b.payout_2027:.0f} expected 2027 payout, ${b.payout_3yr:.0f} over three years",
                        confidence="low", reason="Played through RFA bidding and the auction with every owner's likely plan.",
                        change="Other owners' plans (review them), Gibbs's RFA price, the ESPN ranking order.", made_at=str(b.made_at),
                        headline=f"${b.payout_3yr:.0f} over three years", detail=dict(type="offseason")))
    return out


def _ranks_missing() -> bool:
    w = _read("proj_week.csv")
    return bool(len(w)) and "ranks_note" in w and w.ranks_note.astype(str).str.contains("not out yet").any()


def posture() -> dict:
    p = _read("decide_deadline_posture.csv").set_index("owner") if len(_read("decide_deadline_posture.csv")) else None
    s = _read("sim_2026.csv").set_index("owner")
    me = s.loc["Jordan"]
    h = _read("sim_2026_history.csv")
    moved = None
    if len(h) > 10:
        j = h[h.owner == "Jordan"].sort_values("as_of")
        if len(j) >= 2:
            a, b = j.iloc[-2], j.iloc[-1]
            moved = dict(since=str(a.as_of)[:10], p_first=round(b.p_first - a.p_first, 3),
                         p_title=round(b.p_playoff_champ - a.p_playoff_champ, 3), payout=round(b.exp_payout - a.exp_payout))
    return dict(p_first=round(me.p_first, 3), p_top4=round(me.p_top4, 3), p_title=round(me.p_playoff_champ, 3),
                exp_payout=round(me.exp_payout), mean_wins=round(me.mean_wins, 1),
                stance=(p.posture.get("Jordan") if p is not None else None), moved=moved,
                league=[dict(owner=o, p_first=round(r.p_first, 3), p_top4=round(r.p_top4, 3), p_title=round(r.p_playoff_champ, 3),
                             exp_payout=round(r.exp_payout), posture=(p.posture.get(o) if p is not None else None))
                        for o, r in s.sort_values("exp_payout", ascending=False).iterrows()])


def team() -> dict:
    out = {}
    try:
        from dfo.rules.cap import cap_room
        c = cap_room().set_index("owner").loc["Jordan"]
        out["cap"] = dict(room=int(c.room), salary=int(c.salary), dead=int(c.dead))
    except Exception:
        pass
    r = _read("site_rosters.csv")
    if len(r):
        out["roster_size"] = int((r.owner == "Jordan").sum())
    o = _read("decide_outlook_jordan.csv")
    if len(o):
        out["outlook"] = [dict(season=int(x.season), cap_room=int(x.cap_room), strength=round(x.strength)) for x in o.itertuples()]
    return out


def market() -> list[dict]:
    t = _read("decide_deadline_targets.csv")
    return [dict(player=r.player, owner=r.owner, pos=r.pos, gain=r.your_lineup_gain, salary=int(r.salary_2026), rental=bool(r.rental))
            for r in t.head(8).itertuples()] if len(t) else []


def questions() -> list[dict]:
    q = pd.read_csv(JOURNAL / "questions.csv").fillna("")
    return q.to_dict("records")


def health() -> list[dict]:
    src = load_config("sources")["sources"]
    stamps = {}
    r = _read("site_rosters.csv")
    if len(r):
        stamps["league_site"] = r.as_of.max()
    res = _read("results_2026.csv")
    if len(res):
        done = res[res.score_a.notna()]
        stamps["yahoo_league"] = f"through Week {int(done.week.max())}"
    pw = _read("proj_week.csv")
    if len(pw):
        stamps["nflverse"] = pw.stats_as_of.iloc[0]
    out = []
    for k, v in src.items():
        if k not in stamps:
            continue
        lim = v.get("stale_after")
        lim = lim.get("default") if isinstance(lim, dict) else lim
        hrs = _age_hours(stamps[k]) if not str(stamps[k]).startswith("through") else None
        lim_h = (float(str(lim)[:-1]) * (24 if str(lim).endswith("d") else 1)) if lim else None
        out.append(dict(source=k, as_of=str(stamps[k]), age_hours=hrs, limit=str(lim),
                        stale=bool(hrs is not None and lim_h is not None and hrs > lim_h)))
    # FantasyPros weekly ranks: the projections lean on them (weekly 50%, rest of season 50%). They come out
    # Wednesday to Friday; stale when the coming week has none by Saturday, or the latest is 2+ weeks old.
    ew = _read("ecr_weekly_recent.csv")
    if len(ew) and len(pw):
        ew = ew[ew.season == ew.season.max()]
        lw, wk = int(ew.week.max()), int(pw.week.iloc[0])
        now = pd.Timestamp(_now()).tz_convert("America/Chicago")
        late = (lw < wk and now.weekday() >= 5) or lw < wk - 1
        out.append(dict(source="fantasypros_weekly_ranks", as_of=f"Week {lw} (scraped {str(ew.scrape_date.max())[:10]})",
                        age_hours=_age_hours(ew.scrape_date.max()), limit="the coming week's ranks by Saturday", stale=bool(late),
                        note=(f"Week {wk} ranks missing: weekly projections run without ranks (weaker) and rest-of-season "
                              f"rates use Week {lw}'s" if lw < wk else "current week ranked")))
    rl = _read("run_log.csv")
    if len(rl):
        last = rl[rl.time == rl.time.max()].iloc[0]
        bad = rl.tail(40)[rl.tail(40).status != "ok"]
        out.append(dict(source="engine runs", as_of=str(rl.time.max()), age_hours=_age_hours(rl.time.max()),
                        limit="24h", stale=bool((_age_hours(rl.time.max()) or 0) > 30),
                        note=(f"{len(bad)} step(s) not ok in recent runs" if len(bad) else "all recent steps ok")))
    return out


def journal(items: list[dict]) -> None:
    f = JOURNAL / "recommendations.csv"
    old = pd.read_csv(f) if f.exists() else pd.DataFrame()
    stamp = _now().isoformat(timespec="seconds")
    new = pd.DataFrame([dict(logged_at=stamp, id=i["id"], kind=i["kind"], title=i["title"], recommendation=i["recommendation"],
                             value=i.get("value", ""), confidence=i.get("confidence", ""), reason=i.get("reason", ""),
                             deadline=i.get("deadline", ""), inputs_made_at=i.get("made_at", "")) for i in items])
    if len(old):
        last = old.sort_values("logged_at").groupby("id").tail(1).set_index("id")
        keep = [r for r in new.itertuples() if r.id not in last.index or last.loc[r.id, "recommendation"] != r.recommendation]
        new = new[new.id.isin([r.id for r in keep])]
    if len(new):
        pd.concat([old, new], ignore_index=True).to_csv(f, index=False)


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    items = decisions()
    brief = dict(made_at=_now().isoformat(timespec="seconds"), week=_week(), decisions=items, posture=posture(), team=team(),
                 market=market(), questions=[q for q in questions() if q["status"] == "open"], health=health())
    for k in ("season", "future"):   # dfo.frontdoor.views
        f = OUT / f"{k}.json"
        if f.exists():
            brief[k] = json.loads(f.read_text())
    (OUT / "brief.json").write_text(strict.dumps(brief, indent=1))
    journal(items)
    return brief


if __name__ == "__main__":
    b = run()
    print(json.dumps(dict(decisions=len(b["decisions"]), questions=len(b["questions"]), health=[h["source"] + (" STALE" if h["stale"] else "") for h in b["health"]]), indent=1))
