"""Save what Jordan did on the front door into the repo (append-only journal).

    PYTHONPATH=src python -m dfo.frontdoor.ingest <dump.json>

`dump.json` is the page store as read by the scheduled run: {"responses": [...], "answers": [...],
"notes": [...]}, each row with its id. Responses (agree / disagree and a reason, per decision) go to
data/journal/responses.csv; answers close the matching question in data/journal/questions.csv; notes,
corrections, his own questions and uploads go to data/journal/notes.csv. Rows already saved are skipped,
so running it twice changes nothing. What a note asks for is classified by the next session (rule,
principle, parameter or observation: CLAUDE.md), never applied automatically.
"""
from __future__ import annotations

import json
import sys

import pandas as pd

from dfo.common import ROOT

J = ROOT / "data" / "journal"


def _append(name: str, rows: list[dict], key: str = "id") -> int:
    f = J / name
    old = pd.read_csv(f) if f.exists() else pd.DataFrame()
    new = pd.DataFrame(rows)
    if not len(new):
        return 0
    if len(old) and key in old:
        new = new[~new[key].astype(str).isin(old[key].astype(str))]
    if len(new):
        pd.concat([old, new], ignore_index=True).to_csv(f, index=False)
    return len(new)


def run(dump: dict) -> dict:
    J.mkdir(parents=True, exist_ok=True)
    resp = [dict(id=f"{r['id']}@{r.get('at', '')}", decision_id=r["id"], verdict=r.get("verdict", ""), reason=r.get("reason", ""),
                 recommendation=r.get("recommendation", ""), at=r.get("at", "")) for r in dump.get("responses", [])]
    n_resp = _append("responses.csv", resp)
    q = pd.read_csv(J / "questions.csv", dtype=str).fillna("")
    n_ans = 0
    for a in dump.get("answers", []):
        m = (q.id == a["id"]) & (q.answer.astype(str) != str(a.get("answer", "")))
        if m.any():
            q.loc[m, ["answer", "answered_at", "status"]] = [a.get("answer", ""), a.get("at", ""), "answered"]
            n_ans += int(m.sum())
    q.to_csv(J / "questions.csv", index=False)
    notes = [dict(id=n["id"], kind=n.get("kind", "note"), text=n.get("text", ""),
                  asset_id=";".join(n.get("asset_ids") or ([n["asset_id"]] if n.get("asset_id") else [])),
                  filename=n.get("filename", ""), at=n.get("at", ""), status="new") for n in dump.get("notes", [])]
    n_notes = _append("notes.csv", notes)
    # Discuss threads: one journal row per message (id = decision@time@role), so a conversation is kept
    # even when the page later overwrites the thread document
    msgs = [dict(id=f"{t['id']}@{m.get('at', '')}@{m.get('role', '')}", decision_id=t["id"], role=m.get("role", ""),
                 text=m.get("text", ""), at=m.get("at", ""), thread_status=t.get("status", ""))
            for t in dump.get("threads", []) for m in (t.get("messages") or [])]
    n_threads = _append("threads.csv", msgs)
    return dict(responses=n_resp, answers=n_ans, notes=n_notes, threads=n_threads)


if __name__ == "__main__":
    print(run(json.loads(open(sys.argv[1]).read())))
