"""Shared helpers: repo paths, config loading, owner and player name normalization."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"


@lru_cache(maxsize=None)
def load_config(name: str) -> dict:
    with open(CONFIG / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("’", "'").replace("‘", "'").replace("”", '"').replace("“", '"')
    return "".join(c for c in s if not unicodedata.combining(c))


@lru_cache(maxsize=None)
def _owner_lookup() -> dict[str, str]:
    owners = load_config("owners")["owners"]
    out = {}
    for name, info in owners.items():
        out[name.lower()] = name
        for alias in info.get("aliases", []) or []:
            out[alias.lower()] = name
    return out


def canon_owner(raw: str | None) -> str | None:
    """Map an owner spelling ('Russell', 'jordan', 'Q') to the canonical owner name."""
    if raw is None:
        return None
    key = _fold(str(raw)).strip().lower()
    return _owner_lookup().get(key)


def owners_list() -> list[str]:
    return list(load_config("owners")["owners"].keys())


@lru_cache(maxsize=None)
def _team_index() -> list[tuple[str, int, int, str]]:
    rows = []
    for t in load_config("owners")["team_names"]:
        seasons = t["seasons"]
        start, end = (seasons[0], seasons[-1])
        rows.append((_fold(t["name"]).strip().lower(), start, end, t["owner"]))
    return rows


def team_owner(team_name: str, season: int) -> str | None:
    """Owner of a team name in a given season, or None if unmapped."""
    key = _fold(team_name).strip().lower()
    hits = [o for (n, s, e, o) in _team_index() if n == key and s <= season <= e]
    if len(hits) == 1:
        return hits[0]
    return None


_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)


def player_key(name: str) -> str:
    """Loose key for matching player names across sources (case, punctuation, suffixes ignored)."""
    s = _fold(name).lower()
    s = _SUFFIX.sub("", s)
    return re.sub(r"[^a-z]", "", s)
