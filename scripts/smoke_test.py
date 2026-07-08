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
        "utils",
        "tools",
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
        hero_item_builds,
    )

    assert len(hero_data) > 100, "too few heroes"
    assert len(matchup_data) > 100, "too few matchups"
    assert pos_data and item_data and item_displayName_to_id and patch_data
    assert hero_item_builds, "hero_item_builds empty"


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
    import tools as t

    d = t.get_match_detail(PARSED_MATCH, CARRY_ACC)
    assert d["hero"] == "Drow Ranger"
    assert d["parsed"] is True
    assert d["side"] in ("radiant", "dire")
    assert d["hero"] in d["ally_heroes"]
    assert d["hero"] not in d["enemy_heroes"]
    assert "largest_team_lead" in d and "largest_team_deficit" in d
    assert d["largest_team_lead"] is None or " at " in d["largest_team_lead"]


def _combat_timings():
    import tools as t

    ct = t.get_combat_timings(PARSED_MATCH, CARRY_ACC)
    assert ct["parsed"] is True
    assert len(ct["deaths_detail"]) <= ct["total_deaths"]
    contexts = {
        "caught_alone",
        "skirmish",
        "first_death_of_teamfight",
        "died_in_teamfight",
    }
    for d in ct["deaths_detail"]:
        assert d["context"] in contexts
        r = d["networth_rank"]
        assert r is None or (r.endswith(" of 10") and 1 <= int(r.split()[0]) <= 10)
    swings = ct["gold_swings"]
    assert isinstance(swings, str) and swings.endswith(" (end)"), swings
    pts = swings[: -len(" (end)")].split("; ")
    minutes = [int(p.split("m:")[0]) for p in pts]
    values = [int(p.split(": ")[1]) for p in pts]
    assert minutes == sorted(minutes) and len(values) == len(minutes)
    assert isinstance(ct["enemy_buildings_killed"], list)


def _building_kills_parse():
    import tools as t

    match = {
        "objectives": [
            {"type": "building_kill", "key": "npc_dota_goodguys_tower2_mid", "player_slot": 130, "time": 1998},
            {"type": "building_kill", "key": "npc_dota_badguys_tower1_top", "player_slot": 2, "time": 700},
            {"type": "building_kill", "key": "npc_dota_badguys_tower1_bot", "time": 861},
            {"type": "building_kill", "key": "npc_dota_goodguys_melee_rax_bot", "player_slot": 130, "time": 2672},
            {"type": "building_kill", "key": "npc_dota_goodguys_tower4", "player_slot": 130, "time": 2900},
            {"type": "building_kill", "key": "npc_dota_goodguys_fort", "player_slot": 130, "time": 3000},
        ]
    }
    ks = t._building_kills(match, {"player_slot": 130})
    assert ks == [
        (1998, "mid tier2 tower"),
        (2672, "bot melee rax"),
        (2900, "tier4 tower"),
        (3000, "ancient"),
    ], ks
    assert t._building_kills(match, {"player_slot": 2}) == [(700, "top tier1 tower")]


def _tower_damage_and_stats_fallback():
    import tools as t
    import utils

    assert t._tower_damage_between({}, 0, 600) is None
    stats = {"towerDamagePerMinute": [0, 100, 200, 0, 50]}
    assert t._tower_damage_between(stats, 60, 180) == 300
    assert t._tower_damage_between(stats, 0, 6000) == 350

    def boom(q):
        raise RuntimeError("no key")

    real = utils._stratz
    utils._stratz = boom
    try:
        utils._match_stats_cache.pop(999, None)
        assert utils._stratz_match_stats(999) == {}
    finally:
        utils._stratz = real
        utils._match_stats_cache.pop(999, None)


def _farm_droughts_synthetic():
    import tools as t

    # 0-4 farmed, 5-9 dry (5 mins), 10+ farmed; a fight at minute 6-7.
    lh = [i * 8 for i in range(5)]
    lh += [lh[-1] + i for i in range(1, 6)]
    lh += [lh[-1] + 10 * i for i in range(1, 4)]
    match = {
        "players": [],
        "teamfights": [{"start": 360, "end": 430}],
        "radiant_gold_adv": [0] * len(lh),
    }
    player = {"player_slot": 0, "hero_id": 1, "lh_t": lh}
    d = t._farm_droughts(match, player)
    assert len(d) == 1, d
    assert d[0]["from_min"] == 4 and d[0]["to_min"] == 9, d
    assert d[0]["teamfight_in_span"] is True and d[0]["died_in_span"] is False


def _farm_pattern_live():
    import tools as t

    fp = t.get_farm_pattern(PARSED_MATCH, CARRY_ACC)
    assert fp["parsed"] is True
    cps = fp["cs_checkpoints"]
    assert cps and cps[0]["minute"] == 10
    assert all({"minute", "last_hits", "target", "met"} <= set(c) for c in cps)
    assert isinstance(fp["farm_droughts"], list)


def _break_dispel_lists():
    import tools as t
    from data_loader import item_data

    breakable, dispellable = t._break_dispel_lists([77, 104])  # Lycan, LC
    assert "Feral Impulse" in breakable.get("Lycan", [])
    assert "Moment of Courage" in breakable.get("Legion Commander", [])
    assert "Press The Attack" in dispellable.get("Legion Commander", [])
    assert item_data["item_ghost"].get("basic_dispellable") is True
    assert not item_data["item_orchid"].get("basic_dispellable")


def _compute_metrics():
    import tools as t

    m = t.compute_metrics(PARSED_MATCH, CARRY_ACC)
    assert "metrics" in m and isinstance(m["weak_areas"], list)
    tfp = m["teamfight_participation_pct"]
    assert tfp is None or 0 <= tfp <= 100


def _objectives_and_buybacks_synthetic():
    import tools as t

    match = {
        "players": [
            {
                "player_slot": 0,
                "hero_id": 6,
                "buyback_log": [{"time": 450}],
                "kills_log": [],
            },
            {
                "player_slot": 128,
                "hero_id": 1,
                "kills_log": [
                    {"time": 100, "key": "npc_dota_hero_drow_ranger"},
                    {"time": 400, "key": "npc_dota_hero_drow_ranger"},
                ],
            },
        ],
        "teamfights": [],
        "objectives": [
            {"time": 1200, "type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 3},
            {"time": 1260, "type": "CHAT_MESSAGE_AEGIS", "player_slot": 128},
            {"time": 1500, "type": "CHAT_MESSAGE_MINIBOSS_KILL", "team": 2},
        ],
        "radiant_gold_adv": [0] * 30,
    }
    me = match["players"][0]
    dd = t._death_details(match, me)
    assert [d["bought_back"] for d in dd] == [False, True], dd
    tl = t._objective_timeline(match, me)
    assert tl == [
        "20m Roshan killed by enemy team",
        "21m aegis to Anti-Mage",
        "25m tormentor killed by ally team",
    ], tl


def _lane_matchup():
    import tools as t

    lm = t.score_lane_matchup(PARSED_MATCH, CARRY_ACC)
    assert "advantage" in lm
    assert lm["breakdown"]["ally_lane"], "no heroes resolved for player's lane"
    ally_pos = {h.pos for h in t._lane_heroes([{"lane": 1, "hero_id": 6}], 1, True).values()}
    enemy_pos = {h.pos for h in t._lane_heroes([{"lane": 1, "hero_id": 69}], 1, False).values()}
    assert ally_pos == {"1"} and enemy_pos == {"3"}, (ally_pos, enemy_pos)
    outcome = lm["lane_outcome"]["ally_lane"]
    assert outcome, "no lane_outcome"
    assert all("denies_at_10" in v for v in outcome.values())


def _draft_advantage():
    import tools as t

    da = t.get_draft_advantage(PARSED_MATCH, CARRY_ACC)
    assert "advantage" in da and "ally_team_score" in da


def _chat_history_transcript():
    import tempfile

    import chat_session
    from anthropic.types import TextBlock
    from match_review import session_transcript

    # Session files land in a temp dir for the rest of this run, so the checks
    # below never touch (or leave) real sessions in data/sessions/.
    chat_session._DIR = Path(tempfile.mkdtemp(prefix="smoke-sessions-"))

    chat_session.save(9, 9, [{"role": "assistant", "content": [TextBlock(type="text", text="hi")]}])
    loaded = chat_session.load(9, 9)
    assert loaded[0]["content"][0]["text"] == "hi", loaded
    assert chat_session.list_matches(9) == [9]

    assert session_transcript(1, 1) is None
    chat_session.save(
        1,
        1,
        [
            {"role": "user", "content": "Review match_id 1 for account_id 1."},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "x", "name": "t", "input": {}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "x", "content": "{}"}],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "Result: won."}]},
            {"role": "user", "content": "was my build ok?"},
            {"role": "assistant", "content": [{"type": "text", "text": "Yes."}]},
        ],
    )
    t = session_transcript(1, 1)
    assert t["review"] == "Result: won."
    assert t["messages"] == [
        {"role": "user", "text": "was my build ok?"},
        {"role": "assistant", "text": "Yes."},
    ]


def _review_not_saved_when_ungrounded():
    import chat_session
    import match_review

    def fake_run(trace):
        return lambda *a, **k: {
            "text": "x",
            "tool_trace": trace,
            "messages": [
                {"role": "user", "content": "ask"},
                {"role": "assistant", "content": [{"type": "text", "text": "x"}]},
            ],
        }

    real = match_review.run_agent
    real_ensure = match_review.ensure_parsed
    try:
        match_review.ensure_parsed = lambda match_id: True
        errored = [{"tool": "get_match_detail", "input": {}, "result": {"error": "522"}}]
        match_review.run_agent = fake_run(errored)
        match_review.review_match(account_id=3, match_id=3)
        assert chat_session.load(3, 3) is None, "ungrounded review was saved"

        grounded = [{"tool": "get_match_detail", "input": {}, "result": {"hero": "X"}}]
        match_review.run_agent = fake_run(grounded)
        match_review.review_match(account_id=4, match_id=4)
        assert chat_session.load(4, 4) is not None, "grounded review not saved"
    finally:
        match_review.run_agent = real
        match_review.ensure_parsed = real_ensure


def _unparsed_gate_synthetic():
    import match_review
    import utils

    requested = []
    real_get, real_req = utils._get_obj, utils.request_parse
    real_run = match_review.run_agent

    def boom(*a, **k):
        raise AssertionError("run_agent called despite unparsed gate")

    try:
        utils._get_obj = lambda path, params=None: {"version": None}
        utils.request_parse = lambda match_id: requested.append(match_id)
        match_review.run_agent = boom
        assert utils.ensure_parsed(123) is False
        out = match_review.review_match(account_id=1, match_id=123)
        assert out["review"] == match_review.UNPARSED_NOTE
        assert out["tool_trace"] == [] and out["messages"] == []
        chat = match_review.chat_about_match(9, 123, "why did we lose")
        assert chat["reply"] == match_review.UNPARSED_NOTE
        utils._get_obj = lambda path, params=None: {"version": 21}
        assert utils.ensure_parsed(123) is True
    finally:
        utils._get_obj, utils.request_parse = real_get, real_req
        match_review.run_agent = real_run
    assert requested == [123, 123, 123], f"parse requests: {requested}"


# --- API wiring (no LLM call) ----------------------------------------------
def _api_loads():
    sys.path.insert(0, str(ROOT / "api"))
    import main

    paths = {r.path for r in main.app.routes}
    for p in (
        "/api/score-teams",
        "/api/rank-picks",
        "/api/query",
        "/api/review-match",
        "/api/chat",
        "/api/chat-history",
        "/api/review-history",
        "/api/match-draft-score",
    ):
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


def _api_match_draft_score():
    from fastapi.testclient import TestClient
    import main

    c = TestClient(main.app)
    r = c.get("/api/match-draft-score", params={"matchId": PARSED_MATCH})
    assert r.status_code == 200
    body = r.json()
    assert {"radiantScore", "direScore", "delta"} <= set(body)
    assert abs(body["radiantScore"] - body["direScore"] - body["delta"]) < 0.02


def _api_chat_history_empty():
    from fastapi.testclient import TestClient
    import main

    c = TestClient(main.app)
    r = c.get("/api/chat-history", params={"accountId": 2, "matchId": 2})
    assert r.status_code == 200 and r.json() == {"found": False}


def _api_review_history():
    from fastapi.testclient import TestClient
    import main

    c = TestClient(main.app)
    # session 1-1 was saved by the transcript check above
    r = c.get("/api/review-history", params={"accountId": 1})
    assert r.status_code == 200
    reviews = r.json()["reviews"]
    assert reviews == [{"matchId": 1, "snippet": "Result: won."}]
    r = c.get("/api/review-history", params={"accountId": 2})
    assert r.json() == {"reviews": []}


for name, fn in [
    ("imports", _imports),
    ("data files load (all gather outputs)", _data_files),
    ("hero_lookup maps", _hero_lookup),
    ("score_teams", _score_teams),
    ("rank_picks", _rank_picks),
    ("patch_data has displayName", _patch_data_displayname),
    ("match_review: get_match_detail", _match_detail),
    ("match_review: get_combat_timings deaths_detail", _combat_timings),
    ("match_review: building-kill parsing", _building_kills_parse),
    ("match_review: tower damage + stratz fallback", _tower_damage_and_stats_fallback),
    ("match_review: farm droughts (synthetic)", _farm_droughts_synthetic),
    ("match_review: get_farm_pattern", _farm_pattern_live),
    ("match_review: break/dispel ability lists", _break_dispel_lists),
    ("match_review: compute_metrics", _compute_metrics),
    ("match_review: objectives + buybacks (synthetic)", _objectives_and_buybacks_synthetic),
    ("match_review: score_lane_matchup", _lane_matchup),
    ("match_review: get_draft_advantage", _draft_advantage),
    ("chat session transcript", _chat_history_transcript),
    ("review not saved when ungrounded", _review_not_saved_when_ungrounded),
    ("unparsed match gates review + requests parse", _unparsed_gate_synthetic),
    ("api app loads (8 routes)", _api_loads),
    ("api /score-teams endpoint", _api_score_endpoint),
    ("api /match-draft-score endpoint", _api_match_draft_score),
    ("api /chat-history empty", _api_chat_history_empty),
    ("api /review-history", _api_review_history),
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
