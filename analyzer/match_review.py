import os
import re
import json
import time
import requests
import anthropic
from anthropic.types import MessageParam, ToolParam, ToolResultBlockParam
from dotenv import load_dotenv

from counters import get_counter_score
from evaluator import evaluate_hero, Hero
from data_loader import matchup_data, pos_data
from hero_lookup import ID_TO_NAME

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

AGENT_MODEL = "claude-haiku-4-5-20251001"

OPENDOTA = "https://api.opendota.com/api"

# --- OpenDota data layer ----------------------------------------------------

_cache: dict[str, dict | list] = {}


def _get(path: str, params: dict | None = None) -> dict | list:
    key = path + "?" + json.dumps(params or {}, sort_keys=True)
    if key in _cache:
        return _cache[key]
    last_resp = None
    for attempt in range(3):
        last_resp = requests.get(f"{OPENDOTA}{path}", params=params, timeout=20)
        if last_resp.status_code == 429:
            time.sleep(2**attempt)
            continue
        last_resp.raise_for_status()
        data = last_resp.json()
        _cache[key] = data
        return data
    assert last_resp is not None
    last_resp.raise_for_status()
    raise RuntimeError("unreachable: raise_for_status did not raise on 429")


def _get_obj(path: str, params: dict | None = None) -> dict:
    data = _get(path, params)
    assert isinstance(data, dict), f"expected object from {path}"
    return data


def _get_list(path: str, params: dict | None = None) -> list:
    data = _get(path, params)
    assert isinstance(data, list), f"expected list from {path}"
    return data


def _find_player(match: dict, account_id: int | None) -> dict | None:
    players = match.get("players", [])
    if account_id is not None:
        for p in players:
            if p.get("account_id") == account_id:
                return p
    return None


def _is_parsed(player: dict) -> bool:
    return bool(player.get("gold_t")) and player.get("life_state") is not None


def _pct(player: dict, metric: str) -> int | None:
    b = (player.get("benchmarks") or {}).get(metric)
    if b and b.get("pct") is not None:
        return round(b["pct"] * 100)
    return None


# --- Tool implementations -------------------------------------------------
# Tools return summarized dicts, never raw per-minute arrays, to keep agent
# token cost down.


def get_recent_matches(account_id: int, limit: int = 10) -> dict:
    matches = _get_list(f"/players/{account_id}/matches", {"limit": limit})
    out = []
    for m in matches:
        is_radiant = m["player_slot"] < 128
        won = m["radiant_win"] == is_radiant
        out.append(
            {
                "match_id": m["match_id"],
                "hero": ID_TO_NAME.get(m["hero_id"], str(m["hero_id"])),
                "won": won,
                "kda": f"{m['kills']}/{m['deaths']}/{m['assists']}",
                "gpm": m.get("gold_per_min"),
                "duration_min": round(m["duration"] / 60),
            }
        )
    return {"account_id": account_id, "matches": out}


def get_match_detail(match_id: int, account_id: int | None = None) -> dict:
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    parsed = _is_parsed(player)
    radiant = player["player_slot"] < 128
    won = match["radiant_win"] == radiant
    detail = {
        "match_id": match_id,
        "account_id": player.get("account_id"),
        "hero": ID_TO_NAME.get(player["hero_id"], str(player["hero_id"])),
        "hero_id": player["hero_id"],
        "won": won,
        "duration_min": round(match["duration"] / 60),
        "lane_role": {1: "safelane", 2: "mid", 3: "offlane", 4: "jungle"}.get(
            player.get("lane_role", 0), "unknown"
        ),
        "kda": f"{player['kills']}/{player['deaths']}/{player['assists']}",
        "deaths": player["deaths"],
        "gpm": player.get("gold_per_min"),
        "xpm": player.get("xp_per_min"),
        "last_hits": player.get("last_hits"),
        "parsed": parsed,
        "radiant_heroes": [
            ID_TO_NAME.get(p["hero_id"], str(p["hero_id"]))
            for p in match["players"]
            if p["player_slot"] < 128
        ],
        "dire_heroes": [
            ID_TO_NAME.get(p["hero_id"], str(p["hero_id"]))
            for p in match["players"]
            if p["player_slot"] >= 128
        ],
    }
    return detail


def get_hero_benchmarks(hero_id: int) -> dict:
    data = _get_obj("/benchmarks", {"hero_id": hero_id})
    result = data.get("result", {})
    table = {}
    for metric, points in result.items():
        table[metric] = {f"p{int(pt['percentile']*100)}": pt["value"] for pt in points}
    return {"hero": ID_TO_NAME.get(hero_id, str(hero_id)), "benchmarks": table}


def compute_metrics(match_id: int, account_id: int | None = None) -> dict:
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    metrics = {}
    for metric in (
        "gold_per_min",
        "xp_per_min",
        "last_hits_per_min",
        "hero_damage_per_min",
        "tower_damage",
    ):
        pct = _pct(player, metric)
        metrics[metric] = {
            "value": (player.get("benchmarks") or {}).get(metric, {}).get("raw"),
            "percentile": pct,
        }
    weak = [
        m
        for m, v in metrics.items()
        if v["percentile"] is not None and v["percentile"] < 35
    ]
    return {
        "hero": ID_TO_NAME.get(player["hero_id"], str(player["hero_id"])),
        "metrics": metrics,
        "weak_areas": weak,
    }


def get_death_timings(match_id: int, account_id: int | None = None) -> dict:
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    total_deaths = player.get("deaths", 0)
    if not _is_parsed(player):
        return {
            "parsed": False,
            "total_deaths": total_deaths,
            "note": "match not parsed; death minutes unavailable",
        }
    # life_state is a histogram (seconds alive/dying/dead), not a timeline, so
    # only teamfight deaths have known minutes.
    seconds_dead = (player.get("life_state") or {}).get("2", 0)
    order = [p["player_slot"] for p in match["players"]]
    idx = order.index(player["player_slot"])
    teamfight_deaths = []
    for tf in match.get("teamfights", []):
        n = tf["players"][idx].get("deaths", 0)
        if n:
            teamfight_deaths.append({"minute": round(tf["start"] / 60, 1), "deaths": n})
    tf_total = sum(d["deaths"] for d in teamfight_deaths)
    early = sum(d["deaths"] for d in teamfight_deaths if d["minute"] <= 10)
    return {
        "parsed": True,
        "total_deaths": total_deaths,
        "seconds_spent_dead": seconds_dead,
        "teamfight_death_minutes": teamfight_deaths,
        "deaths_outside_teamfights": total_deaths - tf_total,
        "early_teamfight_deaths_pre10": early,
    }


def get_matchup_difficulty(
    hero_id: int, enemy_hero_ids: list[int], pos: int = 1
) -> dict:
    hid = str(hero_id)
    pos_key = str(pos)
    if hid not in matchup_data:
        return {"note": f"no matchup data for hero {hero_id}"}
    rows = []
    total = 0.0
    for eid in enemy_hero_ids:
        ekey = str(eid)
        if ekey not in matchup_data:
            continue
        try:
            score = get_counter_score(
                hid, ekey, matchup_data, pos_key, pos_key, pos_data
            )
        except (KeyError, ZeroDivisionError):
            continue
        total += score
        rows.append(
            {"enemy": ID_TO_NAME.get(eid, ekey), "counter_score": round(score, 1)}
        )
    rows.sort(key=lambda r: r["counter_score"])
    return {
        "hero": ID_TO_NAME.get(hero_id, hid),
        "net_difficulty": round(total, 1),
        "toughest": rows[:3],
    }


def _lane_to_heroes(lane: list[dict]) -> dict:
    return {
        h["pos"]: Hero(
            ID_TO_NAME.get(h["hero_id"], str(h["hero_id"])),
            str(h["hero_id"]),
            0,
            str(h["pos"]),
        )
        for h in lane
    }


def score_lane_matchup(my_lane: list[dict], enemy_lane: list[dict]) -> dict:
    """Score one lane the same way score_teams scores a full draft, but only
    over the heroes in that lane. For a carry that is the safelane (pos 1 + 5)
    against the enemy offlane (pos 3 + 4). Positive favors my_lane."""
    my = _lane_to_heroes(my_lane)
    enemy = _lane_to_heroes(enemy_lane)
    my_score = 0.0
    enemy_score = 0.0
    breakdown = {"my_lane": [], "enemy_lane": []}
    for hero in my.values():
        s = evaluate_hero(hero.id, my, enemy, hero.pos, bypass_check=True)
        my_score += s
        breakdown["my_lane"].append({"hero": hero.name, "score": round(s, 1)})
    for hero in enemy.values():
        s = evaluate_hero(hero.id, enemy, my, hero.pos, bypass_check=True)
        enemy_score += s
        breakdown["enemy_lane"].append({"hero": hero.name, "score": round(s, 1)})
    return {
        "my_lane_score": round(my_score, 1),
        "enemy_lane_score": round(enemy_score, 1),
        "advantage": round(my_score - enemy_score, 1),
        "breakdown": breakdown,
    }


# --- Agent loop -------------------------------------------------------------

TOOLS: list[ToolParam] = [
    {
        "name": "get_recent_matches",
        "description": "List a player's recent matches (hero, win/loss, KDA, GPM). Use first when given an account_id to pick a match worth reviewing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "integer"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "get_match_detail",
        "description": "Core facts for one match: hero, lane role, KDA, GPM/XPM, win/loss, both teams' heroes, and whether the match is parsed. Call this before the analysis tools; its hero_id and enemy heroes feed them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "match_id": {"type": "integer"},
                "account_id": {"type": "integer"},
            },
            "required": ["match_id"],
        },
    },
    {
        "name": "compute_metrics",
        "description": "Performance percentiles for the player's hero in this match (GPM/XPM/last-hits/damage vs OpenDota benchmarks) and a list of weak_areas. Use to decide what to investigate next.",
        "input_schema": {
            "type": "object",
            "properties": {
                "match_id": {"type": "integer"},
                "account_id": {"type": "integer"},
            },
            "required": ["match_id"],
        },
    },
    {
        "name": "get_hero_benchmarks",
        "description": "Bracket-wide percentile distribution for a hero (GPM/XPM/LH per min/damage). Use when you need the full benchmark scale to contextualize a metric.",
        "input_schema": {
            "type": "object",
            "properties": {"hero_id": {"type": "integer"}},
            "required": ["hero_id"],
        },
    },
    {
        "name": "get_death_timings",
        "description": "Death breakdown for the player: total deaths, seconds spent dead, and minutes of deaths that happened in teamfights. Call when deaths look high or farm is low (deaths explain lost farm).",
        "input_schema": {
            "type": "object",
            "properties": {
                "match_id": {"type": "integer"},
                "account_id": {"type": "integer"},
            },
            "required": ["match_id"],
        },
    },
    {
        "name": "get_matchup_difficulty",
        "description": "Draft-difficulty of the player's hero vs the enemy lineup using the counter engine. Call when farm/impact was low and you want to know if the draft (not execution) was the problem. Pass hero_id and the enemy hero ids from get_match_detail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hero_id": {"type": "integer"},
                "enemy_hero_ids": {"type": "array", "items": {"type": "integer"}},
                "pos": {"type": "integer", "default": 1},
            },
            "required": ["hero_id", "enemy_hero_ids"],
        },
    },
    {
        "name": "score_lane_matchup",
        "description": "Score how favorable the player's lane was, using the same win-rate/counter math as full-draft scoring but only over the lane heroes. For a carry, my_lane is the safelane (the carry + the pos-5 support) and enemy_lane is the offlane (pos 3 + pos 4). Positive advantage means the lane was favored on paper; pair this with the actual farm percentiles to separate a hard lane from poor execution. Pass {hero_id, pos} for each hero from get_match_detail.",
        "input_schema": {
            "type": "object",
            "properties": {
                "my_lane": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "hero_id": {"type": "integer"},
                            "pos": {"type": "integer"},
                        },
                        "required": ["hero_id", "pos"],
                    },
                },
                "enemy_lane": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "hero_id": {"type": "integer"},
                            "pos": {"type": "integer"},
                        },
                        "required": ["hero_id", "pos"],
                    },
                },
            },
            "required": ["my_lane", "enemy_lane"],
        },
    },
]

_TOOL_FNS = {
    "get_recent_matches": get_recent_matches,
    "get_match_detail": get_match_detail,
    "compute_metrics": compute_metrics,
    "get_hero_benchmarks": get_hero_benchmarks,
    "get_death_timings": get_death_timings,
    "get_matchup_difficulty": get_matchup_difficulty,
    "score_lane_matchup": score_lane_matchup,
}

SYSTEM_REVIEW = """You are a Dota 2 post-game coach. You investigate one match and produce short, concrete feedback.

You have tools. Decide what to pull based on what you find — do NOT call every tool by reflex.

Investigate contingently:
- Always start with get_match_detail (or get_recent_matches first if given only an account_id).
- Then call compute_metrics to see the percentiles.
- If farm metrics (GPM / last-hits) are weak, the cause matters: check get_death_timings (deaths cost farm) AND consider the lane (a hard lane costs farm). Pick based on the death count in the detail — many deaths point to death timings; few deaths with low farm points to the lane/draft.
- For a hard lane, use score_lane_matchup first: it scores the carry's safelane (the carry + pos-5 support) against the enemy offlane (pos 3 + 4) directly. If the lane was favored but farm still came out low, the problem is execution, not the lane. Use get_matchup_difficulty when the question is the carry vs the whole enemy team, not just the lane.
- If farm is fine but deaths are high, go to get_death_timings, not matchup difficulty.
- If the match is not parsed, say so and work only from the unparsed metrics; don't call get_death_timings.

Write the final review as 3-5 short bullet points: what went well, the main problem, and one concrete fix. Use the numbers (percentiles, minutes). State drops as plainly as gains. No emojis, no bold."""


def _strip_emphasis(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text)
    return text


def review_match(
    account_id: int | None = None,
    match_id: int | None = None,
    max_turns: int = 8,
) -> dict:
    if account_id is None and match_id is None:
        raise ValueError("provide account_id or match_id")
    if match_id is not None:
        ask = f"Review match_id {match_id}" + (
            f" for account_id {account_id}." if account_id else "."
        )
    else:
        ask = f"Review the most recent notable match for account_id {account_id}."

    messages: list[MessageParam] = [{"role": "user", "content": ask}]
    trace = []
    for _ in range(max_turns):
        resp = client.messages.create(
            model=AGENT_MODEL,
            max_tokens=1500,
            system=SYSTEM_REVIEW,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text")
            return {"review": _strip_emphasis(text.strip()), "tool_trace": trace}

        results: list[ToolResultBlockParam] = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            trace.append({"tool": block.name, "input": block.input})
            try:
                out = _TOOL_FNS[block.name](**block.input)
            except Exception as e:
                out = {"error": str(e)}
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(out),
                }
            )
        messages.append({"role": "user", "content": results})

    return {"review": "max turns reached", "tool_trace": trace}
