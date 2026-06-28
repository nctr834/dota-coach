"""Smoke test: imports, data loading, draft math, and the match-review tools
(these call the OpenDota API, which needs no key or LLM tokens).

Run: python3 scripts/smoke_test.py

Skips the Anthropic paths (/api/query, /api/review-match narration),(costs tokens)
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analyzer"))
os.chdir(ROOT)

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, fn):
    try:
        fn()
        results.append((PASS, name, ""))
    except Exception as e:
        results.append((FAIL, name, f"{type(e).__name__}: {e}"))


# --- imports + data loading -------------------------------------------------
def _imports():
    import importlib

    for mod in (
        "data_loader",
        "hero_lookup",
        "counters",
        "evaluator",
        "process_query",
        "match_review",
    ):
        importlib.import_module(mod)


def _data_files():
    from data_loader import (
        hero_data,
        matchup_data,
        pos_data,
        item_data,
        item_displayName_to_id,
        patch_data,
        aghs_data,
        hero_item_builds,
    )

    assert len(hero_data) > 100, "too few heroes"
    assert len(matchup_data) > 100, "too few matchups"
    assert len(aghs_data) > 100, "too few aghs entries"
    assert pos_data and item_data and item_displayName_to_id and patch_data
    assert hero_item_builds, "hero_item_builds empty"


def _aghs_coverage():
    import process_query as pq
    from hero_lookup import NAME_TO_ID

    assert pq._get_aghs_data("Anti-Mage"), "Anti-Mage aghs missing"
    covered = sum(1 for name in NAME_TO_ID if pq._get_aghs_data(name))
    assert covered > 100, f"only {covered} heroes have aghs text"


def _hero_lookup():
    from hero_lookup import ID_TO_NAME, NAME_TO_ID, name_of

    assert ID_TO_NAME[6] == "Drow Ranger"
    assert NAME_TO_ID["Drow Ranger"] == "6"
    assert name_of(6) == "Drow Ranger"
    assert name_of(99999) == "99999"  # unknown id falls back


# --- draft math -------------------------------------------------------------
def _score_teams():
    from evaluator import Hero, score_teams

    t = {"1": Hero("Drow Ranger", "6", 0, "1"), "5": Hero("Tusk", "100", 0, "5")}
    e = {"3": Hero("Lion", "26", 0, "3"), "4": Hero("Witch Doctor", "30", 0, "4")}
    r, d, delta = score_teams(t, e)
    assert abs(delta - (r - d)) < 1e-6


def _rank_picks():
    from evaluator import Hero, rank_picks

    t = {"5": Hero("Tusk", "100", 0, "5")}
    e = {"3": Hero("Lion", "26", 0, "3")}
    picks = rank_picks(t, e, "1")
    assert len(picks) > 20, "expected many candidate picks"
    assert picks[0].score >= picks[-1].score, "not sorted descending"


def _patch_data_displayname():
    from data_loader import patch_data

    assert "displayName" in patch_data["heroes"]["1"]


# --- match_review tools (free OpenDota API) ---------------------------------
PARSED_MATCH = 8869535334
CARRY_ACC = 96183976


def _match_detail():
    import match_review as mr

    d = mr.get_match_detail(PARSED_MATCH, CARRY_ACC)
    assert d["hero"] == "Drow Ranger"
    assert d["parsed"] is True


def _compute_metrics():
    import match_review as mr

    m = mr.compute_metrics(PARSED_MATCH, CARRY_ACC)
    assert "metrics" in m and isinstance(m["weak_areas"], list)


def _lane_matchup():
    import match_review as mr

    lm = mr.score_lane_matchup(PARSED_MATCH, CARRY_ACC)
    assert "advantage" in lm
    assert lm["breakdown"]["ally_lane"], "no heroes resolved for player's lane"


def _draft_advantage():
    import match_review as mr

    da = mr.get_draft_advantage(PARSED_MATCH, CARRY_ACC)
    assert "advantage" in da and "ally_team_score" in da


# --- API wiring (no LLM call) ----------------------------------------------
def _api_loads():
    sys.path.insert(0, str(ROOT / "api"))
    import main

    paths = {r.path for r in main.app.routes}
    for p in ("/api/score-teams", "/api/rank-picks", "/api/query", "/api/review-match"):
        assert p in paths, f"missing route {p}"


def _api_score_endpoint():
    from fastapi.testclient import TestClient
    import main

    c = TestClient(main.app)
    payload = {
        "radiant": [{"displayName": "Drow Ranger", "id": "6", "score": 0, "pos": 1}],
        "dire": [{"displayName": "Lion", "id": "26", "score": 0, "pos": 3}],
    }
    r = c.post("/api/score-teams", json=payload)
    assert r.status_code == 200 and "delta" in r.json()


for name, fn in [
    ("imports", _imports),
    ("data files load (all gather outputs)", _data_files),
    ("aghs coverage (127 heroes)", _aghs_coverage),
    ("hero_lookup maps", _hero_lookup),
    ("score_teams", _score_teams),
    ("rank_picks", _rank_picks),
    ("patch_data has displayName", _patch_data_displayname),
    ("match_review: get_match_detail", _match_detail),
    ("match_review: compute_metrics", _compute_metrics),
    ("match_review: score_lane_matchup", _lane_matchup),
    ("match_review: get_draft_advantage", _draft_advantage),
    ("api app loads (4 routes)", _api_loads),
    ("api /score-teams endpoint", _api_score_endpoint),
]:
    check(name, fn)

print()
for status, name, detail in results:
    line = f"[{status}] {name}"
    if detail:
        line += f"  -> {detail}"
    print(line)

failed = sum(1 for s, _, _ in results if s == FAIL)
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
