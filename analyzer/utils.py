"""Shared infrastructure for the agent tools: OpenDota and Stratz API clients
(with caching), and small data helpers over match and item data."""

import os
import json
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from evaluator import Hero
from data_loader import hero_data, item_data
from hero_lookup import ID_TO_NAME

ROOT = Path(__file__).resolve().parent.parent

_NPC_NAME = {
    int(h["id"]): f"npc_dota_hero_{h['shortName']}" for h in hero_data.values()
}
_NPC_TO_DISPLAY = {
    f"npc_dota_hero_{h['shortName']}": h["displayName"] for h in hero_data.values()
}
_NPC_TO_ID = {_NPC_NAME[int(h["id"])]: int(h["id"]) for h in hero_data.values()}

load_dotenv()
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


OPENDOTA = "https://api.opendota.com/api"

# --- OpenDota data layer ----------------------------------------------------

_cache: dict[str, dict | list] = {}


def _cache_key(path: str, params: dict | None = None) -> str:
    return path + "?" + json.dumps(params or {}, sort_keys=True)


def _get(path: str, params: dict | None = None) -> dict | list:
    key = _cache_key(path, params)
    if key in _cache:
        return _cache[key]
    last_resp = None
    for attempt in range(3):
        last_resp = requests.get(f"{OPENDOTA}{path}", params=params, timeout=20)
        if last_resp.status_code == 429 or last_resp.status_code >= 500:
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


def request_parse(match_id: int) -> int | None:
    """Queue an OpenDota replay parse. Returns the job id, None on failure."""
    try:
        resp = requests.post(f"{OPENDOTA}/request/{match_id}", timeout=20)
        return (resp.json().get("job") or {}).get("jobId")
    except Exception:
        return None


def ensure_parsed(match_id: int) -> bool:
    """True when OpenDota has replay-parsed data for the match. Otherwise
    queues a parse and evicts the cached match blob so a retry refetches."""
    match = _get_obj(f"/matches/{match_id}")
    if match.get("version") is not None:
        return True
    _cache.pop(_cache_key(f"/matches/{match_id}"), None)
    request_parse(match_id)
    return False


def _pct(player: dict, metric: str) -> int | None:
    b = (player.get("benchmarks") or {}).get(metric)
    if b and b.get("pct") is not None:
        return round(b["pct"] * 100)
    return None


# --- Tool implementations -------------------------------------------------
# Tools return summarized dicts, never raw per-minute arrays, to keep agent
# token cost down.


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


_WINDOW_S = 180  # 3 minutes after a timing item to look for fight impact
_DECIDED_GOLD = (
    15000  # team behind by more than this = game essentially decided, timing moot
)


def _gold_adv_at(match: dict, player: dict, minute: int) -> int | None:
    """Player team's gold advantage at a given minute (negative = behind).
    Clamped: pre-horn events have negative times, and adv[-1] would silently
    read the end of the game."""
    adv = match.get("radiant_gold_adv")
    if not adv:
        return None
    radiant = player["player_slot"] < 128
    val = adv[max(0, min(minute, len(adv) - 1))]
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


def _hero(p: dict, pos: str) -> Hero:
    return Hero(
        ID_TO_NAME.get(p["hero_id"], str(p["hero_id"])), str(p["hero_id"]), 0, pos
    )


STRATZ = "https://api.stratz.com/graphql"

# Verified pos-1 carry accounts (OpenDota proPlayers resolved, confirmed via
# Stratz to have recent parsed POSITION_1 matches with item purchases).
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


_match_stats_cache: dict[int, dict] = {}


def _stratz_match_stats(match_id: int) -> dict:
    """Stratz deep-parse stats per player, keyed by steam account id: per-minute
    tower damage (deltas, not cumulative) and the farm gold distribution.
    Empty per player (or entirely) when Stratz lacks the deep parse for this
    match, the API key is missing, or the call fails — every consumer treats
    the fields as optional."""
    if match_id in _match_stats_cache:
        return _match_stats_cache[match_id]
    query = f"""
    {{
    match(id: {match_id}) {{
        players {{
            steamAccountId
            stats {{
                towerDamagePerMinute
                farmDistributionReport {{
                    creepLocation {{ gold }}
                    neutralLocation {{ gold }}
                    ancientLocation {{ gold }}
                    buildings {{ gold }}
                }}
            }}
        }}
    }}
    }}
    """
    try:
        players = (_stratz(query).get("match") or {}).get("players") or []
    except Exception:
        players = []
    stats = {
        p["steamAccountId"]: p.get("stats") or {}
        for p in players
        if p.get("steamAccountId") is not None
    }
    _match_stats_cache[match_id] = stats
    return stats


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
