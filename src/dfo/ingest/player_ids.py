"""One player identity across every source.

    PYTHONPATH=src python -m dfo.ingest.player_ids

Canonical id: the nflverse gsis id when known ("00-0038542"); otherwise "sleeper:<id>"; team defenses
are "DEF-<nflverse team>". Sources joined:
  - DynastyProcess db_playerids (crosswalk: gsis, sleeper, fantasypros, yahoo, pff, ktc, espn, mfl ...)
  - nflverse players (gsis, pff, espn, birth date, draft)
  - league site players (site id = "sl-<sleeper id>" or "def-<team>")
  - FantasyCalc (sleeper id)
Name-only history tables are matched by name, position and the seasons a player was active; anything
ambiguous or unmatched is written out for review instead of guessed.

Outputs: data/processed/player_ids.csv and data/processed/name_matches.csv
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict

from dfo.common import PROCESSED, ROOT, player_key
from dfo.scoring import TEAM_NAMES
from dfo.sources.fetch import dynastyprocess, nflverse

NA = ("", "NA", "None", None)
SITE_TO_NFLVERSE_TEAM = {"LAR": "LA"}   # site defense ids that differ from nflverse
FIELDS = ["canonical_id", "name", "pos", "gsis_id", "sleeper_id", "site_player_id", "fantasypros_id",
          "yahoo_id", "pff_id", "ktc_id", "espn_id", "mfl_id", "fantasycalc_id", "birthdate",
          "draft_year", "draft_round", "draft_pick", "rookie_season", "last_season"]


def _v(x):
    return None if x in NA else str(x)


def build_ids() -> list[dict]:
    people: dict[str, dict] = {}
    by_gsis, by_sleeper = {}, {}

    def upsert(gsis=None, sleeper=None, **kw):
        rec = (by_gsis.get(gsis) if gsis else None) or (by_sleeper.get(sleeper) if sleeper else None)
        if rec is None:
            rec = {f: "" for f in FIELDS}
            people[id(rec)] = rec
        for k, v in dict(gsis_id=gsis, sleeper_id=sleeper, **kw).items():
            if v not in NA and not rec.get(k):
                rec[k] = str(v)
        if rec["gsis_id"]:
            by_gsis[rec["gsis_id"]] = rec
        if rec["sleeper_id"]:
            by_sleeper[rec["sleeper_id"]] = rec
        return rec

    for r in csv.DictReader(open(nflverse("players", "players.csv"), encoding="utf-8")):
        if r["position_group"] in ("QB", "RB", "WR", "TE", "SPEC") or r["position"] in ("QB", "RB", "WR", "TE", "K", "FB"):
            upsert(gsis=_v(r["gsis_id"]), name=r["display_name"], pos=r["position"], pff_id=_v(r["pff_id"]),
                   espn_id=_v(r["espn_id"]), birthdate=_v(r["birth_date"]), draft_year=_v(r["draft_year"]),
                   draft_round=_v(r["draft_round"]), draft_pick=_v(r["draft_pick"]),
                   rookie_season=_v(r["rookie_season"]), last_season=_v(r["last_season"]))
    for r in csv.DictReader(open(dynastyprocess() / "db_playerids.csv", encoding="utf-8")):
        if r["position"] not in ("QB", "RB", "WR", "TE", "K", "PK", "FB"):
            continue
        upsert(gsis=_v(r["gsis_id"]), sleeper=_v(r["sleeper_id"]), name=r["name"],
               pos="K" if r["position"] == "PK" else r["position"],
               fantasypros_id=_v(r["fantasypros_id"]), yahoo_id=_v(r["yahoo_id"]), pff_id=_v(r["pff_id"]),
               ktc_id=_v(r["ktc_id"]), espn_id=_v(r["espn_id"]), mfl_id=_v(r["mfl_id"]),
               birthdate=_v(r["birthdate"]), draft_year=_v(r["draft_year"]))
    site = ROOT / "data" / "snapshots" / "site" / "latest" / "players.json"
    if site.exists():
        for r in json.load(open(site, encoding="utf-8"))["rows"]:
            if r["id"].startswith("def-"):
                continue
            upsert(sleeper=_v(r.get("sleeper_id")) or r["id"].removeprefix("sl-"), site_player_id=r["id"],
                   name=r["name"], pos=r["position"])
    fc = ROOT / "data" / "snapshots" / "market" / "fantasycalc" / "latest.json"
    if fc.exists():
        for r in json.load(open(fc, encoding="utf-8"))["data"]:
            p = r["player"]
            upsert(sleeper=_v(p.get("sleeperId")), fantasycalc_id=_v(p.get("id")), name=p["name"],
                   pos=p.get("position"), mfl_id=_v(p.get("mflId")), espn_id=_v(p.get("espnId")))
    _merge_sleeper_only(people)
    out = []
    for rec in people.values():
        rec["canonical_id"] = rec["gsis_id"] or (f"sleeper:{rec['sleeper_id']}" if rec["sleeper_id"] else "")
        if rec["canonical_id"]:
            out.append(rec)
    for abbr, nick in TEAM_NAMES.items():                     # team defenses
        site_abbr = {v: k for k, v in SITE_TO_NFLVERSE_TEAM.items()}.get(abbr, abbr)
        rec = {f: "" for f in FIELDS}
        rec.update(canonical_id=f"DEF-{abbr}", name=nick, pos="DEF", site_player_id=f"def-{site_abbr}")
        out.append(rec)
    return sorted(out, key=lambda r: r["canonical_id"])


def _merge_sleeper_only(people: dict) -> None:
    """Fold records known only by a Sleeper id into the matching nflverse (gsis) record when the
    match is unambiguous: same name and position, and either the same birth date or exactly one
    gsis candidate active since 2024. Anything else stays separate rather than guessed."""
    by_name = defaultdict(list)
    for k, r in people.items():
        if r["gsis_id"]:
            by_name[(player_key(r["name"]), r["pos"])].append(r)
    for k in list(people):
        r = people[k]
        if r["gsis_id"] or not r["sleeper_id"]:
            continue
        cands = by_name.get((player_key(r["name"]), r["pos"]), [])
        if r["birthdate"]:
            same_dob = [c for c in cands if c["birthdate"] == r["birthdate"]]
            if len(same_dob) == 1:
                cands = same_dob
        if len(cands) > 1:
            recent = [c for c in cands if (c["last_season"] or "0") >= "2024"]
            cands = recent if len(recent) == 1 else cands
        if len(cands) == 1 and not cands[0]["sleeper_id"]:
            target = cands[0]
            for f, v in r.items():
                if v and not target.get(f):
                    target[f] = v
            del people[k]


# ------------------------------------------------------------------ name-only history matching
def _index(ids: list[dict]) -> dict[str, list[dict]]:
    idx = defaultdict(list)
    for r in ids:
        idx[player_key(r["name"])].append(r)
        if r["pos"] == "DEF":                                   # "Bears D", "49ers Def", "Bears"
            idx[player_key(r["name"] + "d")].append(r)
            idx[player_key(r["name"] + "def")].append(r)
    return idx


ALIASES = {  # spelling variants seen in the league's own sheets -> standard name
    "patrickmaholmes": "patrickmahomes", "christianmccaffery": "christianmccaffrey",
    "saquanbarkley": "saquonbarkley", "tyrekehill": "tyreekhill", "latrviousmurray": "lataviusmurray",
    "jerryjuedy": "jerryjeudy", "ceedeelamb": "ceedeelamb", "kylepitts": "kylepitts",
    "devontesmith": "devontasmith", "cortlandsutton": "courtlandsutton", "allanlazard": "allenlazard",
    "marcusmariotta": "marcusmariota", "dorialgreenbeckham": "dorialgreenbeckham",
    "leviskashenault": "laviskashenault", "israelabinkanda": "israelabanikanda",
    "jacesternburger": "jacesternberger", "ezekielelliot": "ezekielelliott",
    "steviejohnson": "stevejohnson", "stephenhauschka": "stevenhauschka",
    "nyheimmillerhines": "nyheimhines", "hollywoodbrown": "marquisebrown", "marquisbrown": "marquisebrown",
    "joshuapalmer": "joshpalmer", "terrencewest": "terrancewest", "jerrickmckinnon": "jerickmckinnon",
    "wendallsmallwood": "wendellsmallwood", "kelanballage": "kalenballage", "dwayneeskridge": "deeeskridge",
    "kennethgainwell": "kennygainwell", "cameronward": "camward", "gabrieldavis": "gabedavis",
    "rashadpenny": "rashaadpenny", "isaiahpacheco": "isiahpacheco", "kennanallen": "keenanallen",
    "austinekler": "austinekeler", "emanuelsanders": "emmanuelsanders",
}
DEF_CITY = {  # "San Fran D", "Houston D" -> defense
    "arizona": "ARI", "atlanta": "ATL", "baltimore": "BAL", "buffalo": "BUF", "carolina": "CAR",
    "chicago": "CHI", "cincinnati": "CIN", "cleveland": "CLE", "dallas": "DAL", "denver": "DEN",
    "detroit": "DET", "greenbay": "GB", "houston": "HOU", "indianapolis": "IND", "jacksonville": "JAX",
    "kansascity": "KC", "losangelesrams": "LA", "losangeleschargers": "LAC", "lasvegas": "LV",
    "miami": "MIA", "minnesota": "MIN", "newengland": "NE", "neworleans": "NO", "newyorkgiants": "NYG",
    "newyorkjets": "NYJ", "philadelphia": "PHI", "pittsburgh": "PIT", "seattle": "SEA", "sanfran": "SF",
    "sanfrancisco": "SF", "tampabay": "TB", "tennessee": "TEN", "washington": "WAS"}


def match_name(name: str, season: int, idx, pos: str | None = None, rookie: bool = False,
               team: str | None = None) -> tuple[str, str]:
    key = player_key(name)
    key = ALIASES.get(key, key)
    if pos in ("DEF", "D") and team and (key in DEF_CITY or key == "losangeles"):
        # 2026 lists defenses by city ("Los Angeles"); the NFL team column settles which one
        t = team.upper()
        return f"DEF-{ {'LAR': 'LA', 'JAC': 'JAX', 'WSH': 'WAS', 'OAK': 'LV', 'SD': 'LAC', 'STL': 'LA'}.get(t, t)}", "city+team"
    for suffix in ("def", "d"):
        if key.endswith(suffix) and key[: -len(suffix)] in DEF_CITY:
            return f"DEF-{DEF_CITY[key[: -len(suffix)]]}", "alias"
    cands = idx.get(key, [])
    if pos:
        p = "DEF" if pos in ("DEF", "D") else ("K" if pos in ("K", "PK") else pos)
        same = [c for c in cands if c["pos"] == p]
        cands = same or cands
    if len(cands) > 1:   # keep players active in that season
        def active(c):
            lo = int(c["rookie_season"] or c["draft_year"] or 0)
            hi = int(c["last_season"] or 9999)
            return (lo == 0 or lo <= season + 1) and hi >= season - 1
        live = [c for c in cands if active(c)]
        cands = live or cands
    if len(cands) > 1 and rookie:   # a rookie-draft name is that year's NFL rookie
        rk = [c for c in cands if str(season) in (c["draft_year"], c["rookie_season"])]
        cands = rk or cands
    if len(cands) > 1:              # prefer the fantasy-relevant player (has a FantasyPros id)
        fp = [c for c in cands if c["fantasypros_id"]]
        cands = fp if len(fp) == 1 else cands
    if len(cands) == 1:
        return cands[0]["canonical_id"], "exact" if player_key(name) == key else "alias"
    return "", "ambiguous" if cands else "unmatched"


def match_history(ids: list[dict]) -> list[dict]:
    idx = _index(ids)
    rows = []
    sources = {
        "auctions": ("player", "pos", "nfl_team"),
        "rookie_drafts": ("player", None, None),
        "rfa_bids": ("player", None, None),
        "designations": ("player", None, None),
    }
    for table, (col, poscol, teamcol) in sources.items():
        seen = set()
        for r in csv.DictReader(open(PROCESSED / f"{table}.csv", encoding="utf-8")):
            name = r[col]
            if not name:
                continue
            season = int(r["season"])
            k = (name, season)
            if k in seen:
                continue
            seen.add(k)
            pos = r.get(poscol) if poscol else None
            cid, how = match_name(name, season, idx, pos, rookie=(table == "rookie_drafts"),
                                  team=r.get(teamcol) if teamcol else None)
            rows.append(dict(table=table, season=season, name=name, pos=pos or "", canonical_id=cid,
                             method=how))
    return rows


def main() -> None:
    ids = build_ids()
    with open(PROCESSED / "player_ids.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(ids)
    matches = match_history(ids)
    with open(PROCESSED / "name_matches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(matches[0].keys()))
        w.writeheader()
        w.writerows(matches)
    print(f"player_ids: {len(ids)} players")
    c = Counter((m["table"], m["method"]) for m in matches)
    for t in ("auctions", "rookie_drafts", "rfa_bids", "designations"):
        tot = sum(v for (tt, _), v in c.items() if tt == t)
        ok = c[(t, "exact")] + c[(t, "alias")]
        print(f"  {t:14s} {ok}/{tot} matched  (ambiguous {c[(t, 'ambiguous')]}, unmatched {c[(t, 'unmatched')]})")


if __name__ == "__main__":
    main()
