import os
import re
import sys
import json
import time
from collections import Counter
import requests
import anthropic
from anthropic.types import MessageParam, ToolParam, ToolResultBlockParam
from dotenv import load_dotenv
from pathlib import Path

from evaluator import evaluate_hero, score_teams, Hero
from data_loader import hero_data, item_data
from hero_lookup import ID_TO_NAME

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analyzer"))
os.chdir(ROOT)

# OpenDota kills_log / killed_by keys use the unit name npc_dota_hero_<shortName>.
_NPC_NAME = {
    int(h["id"]): f"npc_dota_hero_{h['shortName']}" for h in hero_data.values()
}
_NPC_TO_DISPLAY = {
    f"npc_dota_hero_{h['shortName']}": h["displayName"] for h in hero_data.values()
}

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

ITEM_TIMING_CACHE = ROOT / "analyzer" / "item_timing_cache.json"
MATCHUP_BUILDS_CACHE = ROOT / "analyzer" / "matchup_builds_cache.json"

# Set True (e.g. by an eval --fresh run) to recompute the timing cache instead
# of reading it; the agent calls tools with fixed args and cannot pass it.
_FRESH = False


def _load_cache(path: Path) -> dict:
    """The cache dict, or {} if the file is missing, empty, or corrupt (an
    interrupted write leaves a zero-byte file that json.loads would choke on)."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


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
        "kills": player["kills"],
        "deaths": player["deaths"],
        "assists": player["assists"],
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


def _phase_of(seconds: float) -> str:
    if seconds <= 600:
        return "laning_pre10"
    if seconds <= 1500:
        return "mid_10_25"
    return "late_25plus"


def _phase_counts(times: list[float]) -> dict:
    counts = {"laning_pre10": 0, "mid_10_25": 0, "late_25plus": 0}
    for t in times:
        counts[_phase_of(t)] += 1
    return counts


def _deaths(match: dict, player: dict) -> list[tuple[int, str | None]]:
    """(time, killer) for each of the player's deaths, from enemies' kills_log
    keyed to this player's hero unit."""
    radiant = player["player_slot"] < 128
    my_npc = _NPC_NAME.get(player["hero_id"])
    out = []
    for other in match["players"]:
        if (other["player_slot"] < 128) == radiant:
            continue
        killer = ID_TO_NAME.get(other["hero_id"])
        for k in other.get("kills_log") or []:
            if k.get("key") == my_npc:
                out.append((k["time"], killer))
    return out


def get_combat_timings(match_id: int, account_id: int | None = None) -> dict:
    """Kills and deaths broken down by game phase and by opposing hero, computed
    from the parsed kill logs. Returns only these aggregates (no raw timeline),
    so every figure is verifiable. Phases: laning <=10min, mid 10-25, late >25."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    if not _is_parsed(player):
        return {
            "parsed": False,
            "total_kills": player.get("kills", 0),
            "total_deaths": player.get("deaths", 0),
            "note": "match not parsed; phase breakdown unavailable",
        }
    # Player's kills come from their own kills_log.
    kill_times, kills_by_victim = [], {}
    for k in player.get("kills_log") or []:
        kill_times.append(k["time"])
        victim = _NPC_TO_DISPLAY.get(k.get("key"), k.get("key"))
        kills_by_victim[victim] = kills_by_victim.get(victim, 0) + 1

    death_times, deaths_by_killer = [], {}
    for t, killer in _deaths(match, player):
        death_times.append(t)
        deaths_by_killer[killer] = deaths_by_killer.get(killer, 0) + 1

    return {
        "parsed": True,
        "total_kills": player.get("kills", 0),
        "total_deaths": player.get("deaths", 0),
        "seconds_spent_dead": (player.get("life_state") or {}).get("2", 0),
        "kills_by_phase": _phase_counts(kill_times),
        "deaths_by_phase": _phase_counts(death_times),
        "kills_by_victim": kills_by_victim,
        "deaths_by_killer": deaths_by_killer,
    }


_WINDOW_S = 180  # 3 minutes after a timing item to look for fight impact
_DECIDED_GOLD = (
    15000  # team behind by more than this = game essentially decided, timing moot
)


def _gold_adv_at(match: dict, player: dict, minute: int) -> int | None:
    """Player team's gold advantage at a given minute (negative = behind)."""
    adv = match.get("radiant_gold_adv")
    if not adv:
        return None
    radiant = player["player_slot"] < 128
    val = adv[min(minute, len(adv) - 1)]
    return val if radiant else -val


def _lh_gain(player: dict, start: int, end: int) -> int | None:
    """Last hits the player gained between two times, from the per-minute lh_t."""
    lh = player.get("lh_t")
    if not lh:
        return None
    a, b = start // 60, end // 60
    if b >= len(lh):
        return None
    return lh[b] - lh[a]


_TIMING_TOP_N = 5  # this hero's fight items: the top-N by pro conversion rate
_TIMING_MIN_GAMES = 3  # only rank an item pros bought in at least this many games


def _hero_fight_timings(hero_id: int) -> list[dict]:
    """The hero's fight-timing items, learned from pro games: the completed items
    pros most often got a kill within 3 minutes of completing. Returns up to
    _TIMING_TOP_N as [{short, item, converted, games, rate}], highest rate first."""
    cache = _load_cache(ITEM_TIMING_CACHE)
    key = str(hero_id)
    if not _FRESH and key in cache:
        return cache[key]
    converted: dict[str, int] = {}
    games_with: dict[str, int] = {}
    for account_id in CARRY_SEED.values():
        query = f"""
        {{
        player(steamAccountId: {account_id}) {{
            matches(request: {{heroIds: [{hero_id}], isParsed: true, take: 3}}) {{
                players(steamAccountId: {account_id}) {{
                    stats {{
                        itemPurchases {{ itemId time }}
                        killEvents {{ time }}
                    }}
                }}
            }}
        }}
        }}
        """
        matches = (_stratz(query).get("player") or {}).get("matches") or []
        for m in matches:
            stats = m["players"][0]["stats"]
            purchases = stats.get("itemPurchases")
            if not purchases:
                continue
            kill_times = [e["time"] for e in stats.get("killEvents") or []]
            for short in {
                _item_short(p["itemId"])
                for p in purchases
                if _notable(_item_short(p["itemId"]))
            }:
                first = min(
                    p["time"] for p in purchases if _item_short(p["itemId"]) == short
                )
                games_with[short] = games_with.get(short, 0) + 1
                if any(first <= t <= first + _WINDOW_S for t in kill_times):
                    converted[short] = converted.get(short, 0) + 1

    ranked = [
        {
            "short": s,
            "item": (item_data.get(f"item_{s}") or {}).get("displayName", s),
            "converted": converted.get(s, 0),
            "games": g,
            "rate": round(converted.get(s, 0) / g, 2),
        }
        for s, g in games_with.items()
        if g >= _TIMING_MIN_GAMES
    ]
    ranked.sort(key=lambda r: r["rate"], reverse=True)
    cache[key] = ranked[:_TIMING_TOP_N]
    ITEM_TIMING_CACHE.write_text(json.dumps(cache, indent=2))
    return cache[key]


def get_timing_windows(match_id: int, account_id: int | None = None) -> dict:
    """For each fight item the player completed, what happened in the 3 minutes
    after. The fight items are learned from pro games of the same hero (the items
    pros most often got a kill right after completing), not a fixed list. Facts
    only: kills, deaths, the player's teamfight damage, last hits gained, whether
    a teamfight happened that the player dealt no damage in (team_fought_without_me),
    and smoke bought. contestable is False when the team was already decided behind
    (>15k) at completion, so a late item is not flagged. missed_window marks the
    compound pattern worth asking about: contestable, no kills, the player kept
    farming, and the team fought without them. It is a prompt to ask, not a verdict;
    the data cannot show intent."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    if not _is_parsed(player):
        return {"parsed": False, "note": "match not parsed; timing windows unavailable"}

    purchases = {e["key"]: e["time"] for e in player.get("purchase_log") or []}
    kill_times = [k["time"] for k in player.get("kills_log") or []]
    death_times = [t for t, _ in _deaths(match, player)]
    slot_order = [p["player_slot"] for p in match["players"]]
    me_idx = slot_order.index(player["player_slot"])

    timings = _hero_fight_timings(player["hero_id"])
    windows = []
    for timing in timings:
        short, name = timing["short"], timing["item"]
        completed = purchases.get(short)
        if completed is None or completed < 0:
            continue
        end = completed + _WINDOW_S
        minute = completed // 60
        kills = sum(1 for t in kill_times if completed <= t <= end)
        deaths = sum(1 for t in death_times if completed <= t <= end)
        fight_damage = 0
        team_fought_without_me = False
        for tf in match.get("teamfights") or []:
            if not (completed <= tf["start"] <= end or completed <= tf["end"] <= end):
                continue
            my_dmg = tf["players"][me_idx]["damage"]
            fight_damage += my_dmg
            ally_in_fight = any(
                p["damage"] > 0
                for i, p in enumerate(tf["players"])
                if i != me_idx
                and (slot_order[i] < 128) == (player["player_slot"] < 128)
            )
            if my_dmg == 0 and ally_in_fight:
                team_fought_without_me = True
        smoke = any(
            e["key"] == "smoke_of_deceit" and completed <= e["time"] <= end
            for e in player.get("purchase_log") or []
        )
        lh_gained = _lh_gain(player, completed, end)
        adv = _gold_adv_at(match, player, minute)
        contestable = adv is None or adv > -_DECIDED_GOLD
        farmed = lh_gained is not None and lh_gained >= 10
        windows.append(
            {
                "item": name,
                "completed_min": round(completed / 60, 1),
                "pro_fight_rate": timing["rate"],
                "pro_fight_games": timing["games"],
                "contestable": contestable,
                "missed_window": (
                    contestable and kills == 0 and farmed and team_fought_without_me
                ),
                "window_kills": kills,
                "window_deaths": deaths,
                "window_fight_damage": fight_damage,
                "window_last_hits": lh_gained,
                "team_fought_without_me": team_fought_without_me,
                "smoke_bought": smoke,
            }
        )
    missed = []
    for w in windows:
        if not w["missed_window"]:
            continue
        rate = round(w["pro_fight_rate"] * 100)
        games = w["pro_fight_games"]
        mins = int(w["completed_min"])
        secs = round((w["completed_min"] - mins) * 60)
        missed.append(
            {
                "item": w["item"],
                "note": (
                    f"You hit {w['item']} at {mins}:{secs:02d} (pros fight after it "
                    f"~{rate}% of the time across {games} pro games) and in the next "
                    f"3 minutes farmed {w['window_last_hits']} last hits with no kills "
                    f"while your team fought without you. Was there a pickoff to make "
                    f"there, or was the map not set up?"
                ),
            }
        )
    if not windows:
        verdict = "No fight items were completed."
    elif missed:
        verdict = "A fight item timing was not turned into impact (see missed)."
    else:
        verdict = "Capitalized on your fight item timings."
    return {
        "parsed": True,
        "hero_fight_items": [t["item"] for t in timings],
        "verdict": verdict,
        "missed": missed,
        "timing_windows": windows,
    }


# lane: 1=bot, 2=mid, 3=top. Heroes share a lane (and oppose each other) when
# their lane number matches. Within a lane the higher-GPM hero is the core; the
# other is the support. Position is assigned by which lane: bot core is the safe
# carry (1) with a hard support (5); top core is the offlaner (3) with a soft
# support (4); mid is solo (2).
_LANE_POS = {1: ("1", "5"), 3: ("3", "4"), 2: ("2", "2")}


def _lane_heroes(players: list[dict], lane: int) -> dict:
    in_lane = sorted(
        (p for p in players if p.get("lane") == lane),
        key=lambda p: p.get("gold_per_min", 0),
        reverse=True,
    )
    core_pos, sup_pos = _LANE_POS[lane]
    positions = [core_pos, sup_pos]
    heroes = {}
    for p, pos in zip(in_lane[:2], positions):
        heroes[pos] = Hero(
            ID_TO_NAME.get(p["hero_id"], str(p["hero_id"])), str(p["hero_id"]), 0, pos
        )
    return heroes


def score_lane_matchup(match_id: int, account_id: int | None = None) -> dict:
    """Score the player's lane vs the lane they faced, using the same
    win-rate/counter math as full-draft scoring. Lanes are read from the match
    (heroes sharing a lane number fought each other); the caller supplies only
    the match. Positive advantage favors the player's lane."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    lane = player.get("lane")
    if lane not in _LANE_POS:
        return {"note": "player's lane is unknown or jungle; cannot score lane"}
    radiant = player["player_slot"] < 128
    allies = [p for p in match["players"] if (p["player_slot"] < 128) == radiant]
    enemies = [p for p in match["players"] if (p["player_slot"] < 128) != radiant]

    ally = _lane_heroes(allies, lane)
    enemy = _lane_heroes(enemies, lane)
    if not ally or not enemy:
        return {"note": "could not resolve both lanes from match data"}

    ally_score = 0.0
    enemy_score = 0.0
    breakdown = {"ally_lane": [], "enemy_lane": []}
    for hero in ally.values():
        s = evaluate_hero(hero.id, ally, enemy, hero.pos, bypass_check=True)
        ally_score += s
        breakdown["ally_lane"].append({"hero": hero.name, "score": round(s, 1)})
    for hero in enemy.values():
        s = evaluate_hero(hero.id, enemy, ally, hero.pos, bypass_check=True)
        enemy_score += s
        breakdown["enemy_lane"].append({"hero": hero.name, "score": round(s, 1)})
    return {
        "ally_lane_score": round(ally_score, 1),
        "enemy_lane_score": round(enemy_score, 1),
        "advantage": round(ally_score - enemy_score, 1),
        "breakdown": breakdown,
    }


def _hero(p: dict, pos: str) -> Hero:
    return Hero(
        ID_TO_NAME.get(p["hero_id"], str(p["hero_id"])), str(p["hero_id"]), 0, pos
    )


def _team_by_pos(players: list[dict], radiant: bool) -> dict:
    """Assign positions 1-5 from physical lane + last-hits. Mid (lane 2) is pos
    2; in the safelane the higher-last-hit hero is pos 1 and the other pos 5; in
    the offlane pos 3 and pos 4. Safelane is bot (lane 1) for radiant, top (lane
    3) for dire. Anything left over (jungle/unknown lane) fills remaining slots
    by last-hits."""
    safe_lane, off_lane = (1, 3) if radiant else (3, 1)
    heroes: dict[str, Hero] = {}
    used = set()

    def take(lane: int, core_pos: str, sup_pos: str):
        in_lane = sorted(
            (p for p in players if p.get("lane") == lane and id(p) not in used),
            key=lambda p: p.get("last_hits", 0),
            reverse=True,
        )
        for p, pos in zip(in_lane[:2], (core_pos, sup_pos)):
            heroes[pos] = _hero(p, pos)
            used.add(id(p))

    for p in players:
        if p.get("lane") == 2 and id(p) not in used:
            heroes["2"] = _hero(p, "2")
            used.add(id(p))
            break
    take(safe_lane, "1", "5")
    take(off_lane, "3", "4")

    leftover = sorted(
        (p for p in players if id(p) not in used),
        key=lambda p: p.get("last_hits", 0),
        reverse=True,
    )
    for pos in ("2", "1", "3", "4", "5"):
        if not leftover:
            break
        if pos not in heroes:
            heroes[pos] = _hero(leftover.pop(0), pos)
    return heroes


def get_draft_advantage(match_id: int, account_id: int | None = None) -> dict:
    """Score the player's full 5-hero draft vs the enemy draft (whole-team
    synergy and counters), distinct from the single-lane matchup. Positions are
    assigned from physical lane and last-hits. Positive advantage favors the
    player's team."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    radiant = player["player_slot"] < 128
    allies = [p for p in match["players"] if (p["player_slot"] < 128) == radiant]
    enemies = [p for p in match["players"] if (p["player_slot"] < 128) != radiant]

    ally = _team_by_pos(allies, radiant)
    enemy = _team_by_pos(enemies, not radiant)
    ally_score, enemy_score, delta = score_teams(ally, enemy)
    return {
        "ally_team_score": round(ally_score, 1),
        "enemy_team_score": round(enemy_score, 1),
        "advantage": round(delta, 1),
    }


# --- Stratz matchup builds --------------------------------------------------

STRATZ = "https://api.stratz.com/graphql"

# Verified pos-1 carry accounts (OpenDota proPlayers resolved, confirmed via
# Stratz to have recent parsed POSITION_1 matches with item purchases).
CARRY_SEED = {
    "Yatoro": 321580662,
    "Ame": 898754153,
    "skiter": 100058342,
    "Crystallis": 127617979,
    "Nightfall": 124801257,
    "23savage": 375507918,
}

_items: dict[int, dict] = {}

# Completed items as Valve grades them; components, consumables, and recipes
# (the build-up noise dota2protracker hides) fall outside this set.
_COMPLETED_QUALITY = {"common", "rare", "epic", "artifact"}

# The "common" tier holds build-up filler (Wraith Band, Perseverance, Magic
# Wand) alongside real items, so the skipped-items diff uses the higher tiers
# only; full builds still list common items.
_DIFF_QUALITY = {"rare", "epic", "artifact"}
# Some rare-tier items are cheap support pickups (Ring of Basilius, Headdress,
# Urn), not build-vs-enemy choices; a cost floor drops them while keeping the
# cheapest real core item (Falcon Blade, 1125).
_NOTABLE_COST = 1000
# Build-up pieces that recur as noise: when they matter the completed form
# (Sange and Yasha, Yasha and Kaya, Manta) carries the signal, so never suggest
# the raw component.
_COMPONENT_NOISE = {"sange", "yasha", "kaya"}


def _notable(short: str | None) -> bool:
    if short in _COMPONENT_NOISE:
        return False
    v = item_data.get(f"item_{short}")
    if not v or v.get("quality") not in _DIFF_QUALITY:
        return False
    cost = v.get("cost")
    return cost is not None and cost >= _NOTABLE_COST


def _stratz(query: str) -> dict:
    key = os.getenv("STRATZ_API_KEY")
    resp = requests.post(
        STRATZ,
        json={"query": query},
        headers={"Authorization": f"Bearer {key}", "User-Agent": "STRATZ_API"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data") or {}


def _load_items() -> None:
    if _items:
        return
    data = _stratz("{ constants { items { id shortName displayName } } }")
    for it in (data.get("constants") or {}).get("items") or []:
        _items[it["id"]] = it


def _item_name(item_id: int) -> str:
    _load_items()
    return (_items.get(item_id) or {}).get("displayName") or str(item_id)


def _item_short(item_id: int) -> str:
    _load_items()
    return (_items.get(item_id) or {}).get("shortName") or str(item_id)


def _is_completed(item_id: int) -> bool:
    _load_items()
    short = (_items.get(item_id) or {}).get("shortName")
    quality = (item_data.get(f"item_{short}") or {}).get("quality")
    return quality in _COMPLETED_QUALITY


def _player_completed_items(match_id: int, account_id: int | None) -> dict[str, str]:
    """shortName -> displayName for the player's completed items in this match."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    items = {}
    for e in player.get("purchase_log") or []:
        v = item_data.get(f"item_{e['key']}")
        if v and v.get("quality") in _COMPLETED_QUALITY:
            items[e["key"]] = v["displayName"]
    return items


def get_matchup_builds(
    carry_hero_id: int,
    enemy_hero_id: int,
    match_id: int | None = None,
    account_id: int | None = None,
    n: int = 4,
) -> dict:
    """Recent completed-item builds (item + minute) pro pos-1 carries bought on
    carry_hero_id in games where enemy_hero_id was on the opposing team.
    enemy_hero_id can be a support, which is often the more useful matchup. Only
    completed items are shown (no components, consumables, or recipes), like
    dota2protracker. Pass match_id/account_id to also get the player's completed
    build and pros_bought_player_skipped: the completed items pros bought here
    that the player did not, the basis for itemization advice."""
    cache = _load_cache(MATCHUP_BUILDS_CACHE)
    key = f"{carry_hero_id}-{enemy_hero_id}"
    if not _FRESH and key in cache:
        builds = cache[key]["builds"]
    else:
        builds = []
        for name, seed_account in CARRY_SEED.items():
            if len(builds) >= n:
                break
            query = f"""
            {{
            player(steamAccountId: {seed_account}) {{
                matches(request: {{
                    heroIds: [{carry_hero_id}],
                    withEnemyHeroIds: [{enemy_hero_id}],
                    isParsed: true,
                    take: 2
                }}) {{
                    id
                    players(steamAccountId: {seed_account}) {{
                        stats {{ itemPurchases {{ itemId time }} }}
                    }}
                }}
            }}
            }}
            """
            matches = (_stratz(query).get("player") or {}).get("matches") or []
            for mm in matches:
                if len(builds) >= n:
                    break
                purchases = mm["players"][0]["stats"].get("itemPurchases")
                if not purchases:
                    continue
                builds.append(
                    {
                        "player": name,
                        "match_id": mm["id"],
                        "items": [
                            {
                                "item": _item_name(p["itemId"]),
                                "minute": round(p["time"] / 60),
                                "short": _item_short(p["itemId"]),
                            }
                            for p in purchases
                            if _is_completed(p["itemId"])
                        ],
                    }
                )
        cache[key] = {
            "carry": ID_TO_NAME.get(carry_hero_id, str(carry_hero_id)),
            "vs": ID_TO_NAME.get(enemy_hero_id, str(enemy_hero_id)),
            "builds": builds,
        }
        MATCHUP_BUILDS_CACHE.write_text(json.dumps(cache, indent=2))

    result = {
        "carry": ID_TO_NAME.get(carry_hero_id, str(carry_hero_id)),
        "vs": ID_TO_NAME.get(enemy_hero_id, str(enemy_hero_id)),
        "builds": builds,
    }
    if match_id is not None:
        player_items = _player_completed_items(match_id, account_id)
        pro_by_short = {i["short"]: i["item"] for b in builds for i in b["items"]}
        bought_in = Counter(s for b in builds for s in {x["short"] for x in b["items"]})
        player_keys = {s for s in player_items if _notable(s)}
        skipped = [s for s in pro_by_short if _notable(s) and s not in player_keys]
        skipped.sort(key=lambda s: bought_in[s], reverse=True)
        result["player_build"] = sorted(player_items.values())
        result["pros_bought_player_skipped"] = [
            {"item": pro_by_short[s], "pro_builds": bought_in[s]} for s in skipped
        ]
    return result


_SHARED_FRAC = 0.4  # bought in this fraction of all pulled games = general gap
_REACTIVE_PER_ENEMY = 2  # most-bought reactive items shown per enemy
# Per-enemy attribution is weak: the pulled games share only that one enemy, so
# their other heroes are uncontrolled. Require this many of a matchup's games to
# buy an item before labelling it, so one off-lineup game cannot create a label.
_REACTIVE_MIN_GAMES = 2


def get_build_gaps(match_id: int, account_id: int | None = None) -> dict:
    """Items pros bought that the player skipped, computed against every enemy
    hero, then split into 'shared' (bought in a large share of all pulled games,
    the player's general build gap) and 'seen_against' (concentrated in one
    enemy's games but rare overall). seen_against is a weak signal, not causal:
    the pulled games share only that one enemy, so the item could be a response to
    another hero in those games. Treat shared as the reliable gap and seen_against
    as suggestive."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    carry_id = player["hero_id"]
    radiant = player["player_slot"] < 128
    enemies = [p for p in match["players"] if (p["player_slot"] < 128) != radiant]

    # Tally each skipped item's purchases across every matchup's games, plus the
    # total games pulled, so 'shared' is total game-share (a generally common item
    # regardless of which matchup it clustered in). per_enemy keeps items bought in
    # >=_REACTIVE_MIN_GAMES of one matchup, the candidates for an enemy-specific note.
    total_games = 0
    bought = Counter()
    per_enemy: dict[str, list[tuple[str, int]]] = {}
    for e in enemies:
        builds = get_matchup_builds(
            carry_id, e["hero_id"], match_id=match_id, account_id=account_id
        )
        skipped = builds.get("pros_bought_player_skipped", [])
        if not skipped:
            continue
        total_games += len(builds["builds"])
        for x in skipped:
            bought[x["item"]] += x["pro_builds"]
        named = [
            (x["item"], x["pro_builds"])
            for x in skipped
            if x["pro_builds"] >= _REACTIVE_MIN_GAMES
        ]
        if named:
            per_enemy[ID_TO_NAME.get(e["hero_id"], str(e["hero_id"]))] = named

    shared = {
        it
        for it, c in bought.items()
        if total_games and c / total_games >= _SHARED_FRAC
    }
    reactive: dict[str, list[str]] = {}
    for enemy, items in per_enemy.items():
        unique = [it for it, _ in items if it not in shared][:_REACTIVE_PER_ENEMY]
        if unique:
            reactive[enemy] = unique
    return {
        "carry": ID_TO_NAME.get(carry_id, str(carry_id)),
        "shared": sorted(shared),
        "seen_against": reactive,
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
        "name": "get_combat_timings",
        "description": "Kills and deaths for the player, broken down by game phase (laning <=10min, mid 10-25, late >25) and by opposing hero. Use to see when the player died or got kills and to whom. Call when deaths look high or farm is low (deaths explain lost farm), or to check early-game state.",
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
        "name": "get_timing_windows",
        "description": "For each fight-enabling item the player completed (Blink, BKB, Manta, etc), whether they converted it into impact in the 3 minutes after: capitalized is True if they got a kill or dealt teamfight damage. contestable is False when the team was already decided behind, so a late item is not a missed window. Use to spot a strong item timing the player did not turn into a fight (a wasted power spike). Reports facts only, not intent.",
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
        "name": "score_lane_matchup",
        "description": "Score how favorable the player's LANE was vs the lane they faced (the 2-4 heroes in that lane), using win-rate/counter math. Reads the actual lane heroes from the match; you supply only the match. Positive advantage = the lane was favored on paper. Use to tell a hard lane apart from poor laning execution.",
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
        "name": "get_draft_advantage",
        "description": "Score the player's whole 5-hero DRAFT vs the enemy draft (full-team synergy and counters), distinct from the single-lane matchup. Reads heroes from the match. Positive advantage = the player's team was favored on paper. Use to tell whether the team comp (not just the lane) was the disadvantage.",
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
        "name": "get_build_gaps",
        "description": "Items pro pos-1 carries bought on the player's hero that the player skipped, computed against every enemy and split into 'shared' (items pros bought in every matchup with data, the player's general build gap) and 'reactive' (items pros bought only against specific enemies, labelled with that enemy). Use for itemization advice without guessing a single target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "match_id": {"type": "integer"},
                "account_id": {"type": "integer"},
            },
            "required": ["match_id"],
        },
    },
]

_TOOL_FNS = {
    "get_recent_matches": get_recent_matches,
    "get_match_detail": get_match_detail,
    "compute_metrics": compute_metrics,
    "get_hero_benchmarks": get_hero_benchmarks,
    "get_combat_timings": get_combat_timings,
    "get_timing_windows": get_timing_windows,
    "score_lane_matchup": score_lane_matchup,
    "get_draft_advantage": get_draft_advantage,
    "get_build_gaps": get_build_gaps,
}

SYSTEM_REVIEW = """You are a Dota 2 post-game coach for a position-1 (carry) player. Review one match and give short, concrete feedback.

The review must read consistently with the result (won from get_match_detail): a loss should never read like a win. State the result and what it came down to once, in the "Result:" line below, and nowhere else.

Investigate, do not dump. Always start with get_match_detail and compute_metrics: that gives the result, KDA, and the farm/damage/last-hit percentiles. Read that profile, then call only the deeper tools that the profile points to. You are diagnosing, not filling a form.

- Low farm (low GPM/LH percentile): find out why. Call score_lane_matchup (was the lane lost on paper?) and get_combat_timings (did deaths cause it, or was it a hard lane / passive play?).
- Good farm but low hero-damage percentile: an impact problem. Call get_timing_windows (missed power spikes?) and get_build_gaps (wrong or missing items?).
- Lost despite a strong individual game: call get_draft_advantage to check whether the draft was the story.
- Clean dominant win with no weak percentile: little to investigate; a short confirmation is enough. Do not pull every tool to manufacture a critique.
Call a tool when a real question needs it, not by default. It is fine to call one deeper tool, several, or none beyond the baseline.

Your value is judgment, not stat-reading. Reason across whatever you gathered: decide what actually decided this game, connect the dimensions (a missed timing that led to the deaths that lost the lead; elite farm that never converted to damage), and tell the player the one or two things that matter. A coach who lists every stat is useless; one who says "your farm was fine, the game turned on X" is not.

Faithfulness is absolute and separate from judgment. Every number you state must come from a tool result, used as given: never compute, round differently, or invent a figure, a kill/death minute, or a stat no tool reported. A positive advantage is favorable, negative unfavorable; keep the sign. Reasoning and opinion on the real numbers is encouraged; inventing numbers is not.

Structure. Always write "Result:" and "Read:". Add a reference block only for a tool you actually called.
- "Result:" won or lost (from get_match_detail), and what it came down to. On a loss never read like a win; a hard lane or losing draft that beat the player is the result, not the player's failure.
- "Read:" your coaching analysis, a few sentences. Synthesize what you investigated; prioritize, do not enumerate. If they played well and lost to the draft, say that.
- "Item timings:" only if you called get_timing_windows. If it has "missed" entries, write each one's "note" verbatim; otherwise write its "verdict" line. Do not compose your own timing sentence.
- "Pro build reference:" only if you called get_build_gaps. The "shared" items, e.g. "Generally consider: Silver Edge, Satanic", then for each enemy in "seen_against" one line as a weak signal, e.g. "Seen in Invoker games: Eye of Skadi" (suggestive, not causal). Item names only.

No emojis, no bold."""


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
            temperature=0,
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
            try:
                out = _TOOL_FNS[block.name](**block.input)
            except Exception as e:
                out = {"error": str(e)}
            trace.append({"tool": block.name, "input": block.input, "result": out})
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(out),
                }
            )
        messages.append({"role": "user", "content": results})

    return {"review": "max turns reached", "tool_trace": trace}
