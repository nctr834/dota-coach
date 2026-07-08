"""Agent tool implementations and the TOOLS / _TOOL_FNS registry."""

import json
from collections import Counter
from statistics import median

from anthropic.types import ToolParam

from evaluator import evaluate_hero, score_teams, Hero
from data_loader import item_data, patch_data, hero_break_dispel, item_tags
from hero_lookup import ID_TO_NAME
import utils
from utils import (
    _NPC_TO_DISPLAY,
    _load_cache,
    ITEM_TIMING_CACHE,
    MATCHUP_BUILDS_CACHE,
    _get_obj,
    _get_list,
    _find_player,
    _is_parsed,
    _pct,
    _phase_counts,
    _deaths,
    _gold_adv_at,
    _lh_gain,
    _WINDOW_S,
    _DECIDED_GOLD,
    _hero,
    _notable,
    _stratz,
    _stratz_match_stats,
    _item_name,
    _item_short,
    _is_completed,
    _COMPLETED_QUALITY,
)


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
    lead = deficit = None
    adv = match.get("radiant_gold_adv")
    if adv:
        vals = [v if radiant else -v for v in adv]
        hi, lo = max(vals), min(vals)
        if hi > 0:
            lead = f"+{hi} at {vals.index(hi)}m"
        if lo < 0:
            deficit = f"{lo} at {vals.index(lo)}m"
    final_rank = None
    nw = [(p.get("net_worth"), p["player_slot"]) for p in match["players"]]
    if all(v is not None for v, _ in nw):
        nw.sort(reverse=True)
        pos = next(i for i, (_, s) in enumerate(nw, 1) if s == player["player_slot"])
        final_rank = f"{pos} of 10"
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
        "throw": player.get("throw"),
        "largest_team_lead": lead,
        "largest_team_deficit": deficit,
        "final_networth_rank": final_rank,
        "parsed": parsed,
        "side": "radiant" if radiant else "dire",
        "ally_heroes": [
            ID_TO_NAME.get(p["hero_id"], str(p["hero_id"]))
            for p in match["players"]
            if (p["player_slot"] < 128) == radiant
        ],
        "enemy_heroes": [
            ID_TO_NAME.get(p["hero_id"], str(p["hero_id"]))
            for p in match["players"]
            if (p["player_slot"] < 128) != radiant
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
    tfp = player.get("teamfight_participation")
    return {
        "hero": ID_TO_NAME.get(player["hero_id"], str(player["hero_id"])),
        "metrics": metrics,
        "teamfight_participation_pct": round(tfp * 100) if tfp is not None else None,
        "weak_areas": weak,
    }


def _nw_rank(match: dict, player: dict, minute: int) -> int | None:
    """Player's net-worth rank among all ten heroes at a minute (1 = richest)."""
    vals = []
    for p in match["players"]:
        g = p.get("gold_t")
        if not g:
            return None
        vals.append((g[max(0, min(minute, len(g) - 1))], p["player_slot"]))
    vals.sort(reverse=True)
    for rank, (_, slot) in enumerate(vals, 1):
        if slot == player["player_slot"]:
            return rank
    return None


def _gold_swings(match: dict, player: dict) -> str | None:
    """Turning points of the team's gold advantage — each peak or valley where
    the trend reversed by 2000+ gold, plus the final minute — as one
    chronological "Xm: +/-N" line. A single string so the arc is read whole:
    consecutive points reverse direction by construction, so any span between
    non-adjacent points seesawed."""
    adv = match.get("radiant_gold_adv")
    if not adv:
        return None
    sign = 1 if player["player_slot"] < 128 else -1
    vals = [v * sign for v in adv]
    points = []
    ext = 0
    trend = 0
    for i in range(1, len(vals)):
        d = vals[i] - vals[ext]
        if trend == 0:
            if abs(d) >= 2000:
                trend = 1 if d > 0 else -1
                ext = i
        elif (d > 0) == (trend > 0) and d != 0:
            ext = i
        elif abs(d) >= 2000:
            points.append(ext)
            trend = -trend
            ext = i
    points.append(len(vals) - 1)
    return "; ".join(f"{i}m: {vals[i]:+d}" for i in points) + " (end)"


def _building_kills(match: dict, player: dict) -> list[tuple[int, str]]:
    """Enemy towers/rax/ancient the player last-hit, as (time_s, name) from the
    objectives log — the only time-resolved building credit OpenDota has."""
    enemy = "goodguys" if player["player_slot"] >= 128 else "badguys"
    out = []
    for o in match.get("objectives") or []:
        if o.get("type") != "building_kill":
            continue
        if o.get("player_slot") != player["player_slot"]:
            continue
        key = o.get("key", "")
        if enemy not in key:
            continue
        lane = next((s for s in ("top", "mid", "bot") if key.endswith("_" + s)), None)
        if "fort" in key:
            base = "ancient"
        elif "melee_rax" in key:
            base = "melee rax"
        elif "range_rax" in key:
            base = "ranged rax"
        elif "tower" in key:
            base = f"tier{key[key.index('tower') + 5]} tower"
        else:
            continue
        out.append((o["time"], f"{lane} {base}" if lane else base))
    return out


def _tower_damage_between(stats: dict, t0: int, t1: int) -> int | None:
    """Player's tower damage dealt between two times, from Stratz per-minute
    deltas. None when the deep parse is missing."""
    arr = (stats or {}).get("towerDamagePerMinute")
    if not arr:
        return None
    return sum(arr[max(0, t0 // 60) : t1 // 60 + 1])


def _death_details(match: dict, player: dict) -> list[dict]:
    """Context for each of the player's deaths (to a hero): caught alone,
    skirmish, or teamfight (dying first flagged), plus how farmed they were
    (net-worth rank), the team gold advantage at the time, and whether the
    player bought back after (each buyback attributed to the death before it)."""
    all_times = sorted(
        k["time"] for p in match["players"] for k in p.get("kills_log") or []
    )
    fights = [
        (f["start"], f["end"])
        for f in match.get("teamfights") or []
        if f.get("start") is not None and f.get("end") is not None
    ]
    death_ts = sorted(t for t, _ in _deaths(match, player))
    bought_back = set()
    for e in player.get("buyback_log") or []:
        prior = [t for t in death_ts if t <= e["time"]]
        if prior:
            bought_back.add(max(prior))
    out = []
    for t, killer in sorted(_deaths(match, player)):
        rank = _nw_rank(match, player, round(t / 60))
        window = next((w for w in fights if w[0] <= t <= w[1]), None)
        if window:
            in_window = [x for x in all_times if window[0] <= x <= window[1]]
            first = t <= min(in_window)
            context = "first_death_of_teamfight" if first else "died_in_teamfight"
        else:
            near = [x for x in all_times if abs(x - t) <= 20]  # maybe change
            near.remove(t)
            context = "skirmish" if near else "caught_alone"
        minute = round(t / 60)
        out.append(
            {
                "minute": minute,
                "killed_by": killer,
                "context": context,
                "networth_rank": f"{rank} of 10" if rank else None,
                "team_gold_adv": _gold_adv_at(match, player, minute),
                "bought_back": t in bought_back,
            }
        )
    return out


_OBJECTIVE_LABEL = {
    "CHAT_MESSAGE_ROSHAN_KILL": "Roshan killed",
    "CHAT_MESSAGE_MINIBOSS_KILL": "tormentor killed",
}


def _objective_timeline(match: dict, player: dict) -> list[str]:
    """Roshan and tormentor kills plus aegis pickups as one chronological list,
    sides relative to the player ('ally'/'enemy')."""
    radiant = player["player_slot"] < 128
    slot_hero = {
        p["player_slot"]: ID_TO_NAME.get(p["hero_id"], str(p["hero_id"]))
        for p in match["players"]
    }
    out = []
    for o in match.get("objectives") or []:
        minute = max(0, o.get("time", 0)) // 60
        label = _OBJECTIVE_LABEL.get(o.get("type"))
        if label:
            if o.get("team") in (2, 3):
                side = "ally" if (o["team"] == 2) == radiant else "enemy"
            elif o.get("player_slot") is not None:
                side = (
                    "ally" if (o["player_slot"] < 128) == radiant else "enemy"
                )
            else:
                side = "unknown"
            out.append(f"{minute}m {label} by {side} team")
        elif o.get("type") == "CHAT_MESSAGE_AEGIS" and o.get("player_slot") is not None:
            out.append(f"{minute}m aegis to {slot_hero.get(o['player_slot'], '?')}")
    return out


def get_combat_timings(match_id: int, account_id: int | None = None) -> dict:
    """Kills and deaths broken down by game phase and by opposing hero, computed
    from the parsed kill logs. Returns only these aggregates (no raw timeline),
    so every figure is verifiable. Phases: laning <=10min, mid 10-25, late >25.
    deaths_detail gives each death's context — caught_alone, skirmish, or a
    teamfight with first_death_of_teamfight flagged — with the player's
    net-worth rank (1 = richest hero in the game) and the team's gold advantage
    at the time: the evidence for judging whether deaths threw the game.
    gold_swings is one chronological line of the advantage's turning points
    (minute: value of each reversal, ending at the final state).
    enemy_buildings_killed is the towers/rax the player last-hit, with minutes,
    and tower_damage_by_phase (Stratz deep parse, when available) the damage
    dealt to buildings per phase — together the evidence separating
    split-pushing from pure farming. buybacks lists the minutes the player
    bought back (each also flagged on its death), and objective_timeline the
    Roshan/tormentor kills and aegis pickups, sides relative to the player."""
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

    arr = (_stratz_match_stats(match_id).get(player.get("account_id")) or {}).get(
        "towerDamagePerMinute"
    )
    tower_by_phase = None
    if arr:
        tower_by_phase = {
            "laning_pre10": sum(arr[:11]),
            "mid_10_25": sum(arr[11:26]),
            "late_25plus": sum(arr[26:]),
        }

    return {
        "parsed": True,
        "total_kills": player.get("kills", 0),
        "total_deaths": player.get("deaths", 0),
        "seconds_spent_dead": (player.get("life_state") or {}).get("2", 0),
        "kills_by_phase": _phase_counts(kill_times),
        "deaths_by_phase": _phase_counts(death_times),
        "kills_by_victim": kills_by_victim,
        "deaths_by_killer": deaths_by_killer,
        "deaths_detail": _death_details(match, player),
        "gold_swings": _gold_swings(match, player),
        "enemy_buildings_killed": [
            f"{t // 60}m {name}" for t, name in _building_kills(match, player)
        ],
        "tower_damage_by_phase": tower_by_phase,
        "buybacks": [e["time"] // 60 for e in player.get("buyback_log") or []],
        "objective_timeline": _objective_timeline(match, player),
    }


_TIMING_TOP_N = 5  # this hero's fight items: the top-N by pro conversion rate
_TIMING_MIN_GAMES = 3  # only rank an item pros bought in at least this many games


def _hero_fight_timings(hero_id: int) -> list[dict]:
    """The hero's fight-timing items, learned from pro games: the completed items
    pros most often got a kill within 3 minutes of completing. Returns up to
    _TIMING_TOP_N as [{short, item, converted, games, rate}], highest rate first."""
    cache = _load_cache(ITEM_TIMING_CACHE)
    key = str(hero_id)
    if not utils._FRESH and key in cache:
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
    enemy buildings the player took in the window (window_buildings_taken — split
    pushing, not pure farming), tower damage dealt in the window
    (window_tower_damage, Stratz deep parse, None when unavailable), and smoke
    bought. contestable is False when the team was already decided behind
    (>15k) at completion, so a late item is not flagged. deaths_before_completion
    lists the minutes the player died between 10:00 and finishing the item —
    fighting before the timing was online, visible per item. missed_window marks the
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
    bkills = _building_kills(match, player)
    stratz_stats = _stratz_match_stats(match_id).get(player.get("account_id")) or {}
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
                "deaths_before_completion": sorted(
                    round(t / 60) for t in death_times if 600 <= t < completed
                ),
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
                "window_buildings_taken": [
                    n for t, n in bkills if completed <= t <= end
                ],
                "window_tower_damage": _tower_damage_between(
                    stratz_stats, completed, end
                ),
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
        towers = w["window_buildings_taken"]
        tdmg = w["window_tower_damage"]
        if towers:
            tail = (
                f"while your team fought without you, but took "
                f"{', '.join(towers)}. Did the buildings cover what the fight cost?"
            )
        elif tdmg is not None and tdmg >= 500:
            tail = (
                f"while your team fought without you, but dealt {tdmg} tower "
                f"damage in that span. Did the tower pressure cover what the "
                f"fight cost?"
            )
        else:
            tail = (
                "while your team fought without you. Was there a pickoff to make "
                "there, or was the map not set up?"
            )
        missed.append(
            {
                "item": w["item"],
                "note": (
                    f"You hit {w['item']} at {mins}:{secs:02d} (pros fight after it "
                    f"~{rate}% of the time across {games} pro games) and in the next "
                    f"3 minutes farmed {w['window_last_hits']} last hits with no kills "
                    + tail
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


# Pos-1 last-hit bands at checkpoint minutes; below the low bound = missed.
_CS_TARGETS = [
    (10, 50, 60),
    (15, 100, 150),
    (20, 150, 200),
    (25, 200, 250),
    (30, 250, 300),
    (40, 300, None),
]
_DROUGHT_LH = 1  # a minute gaining <= this many last hits counts as dry
_DROUGHT_MIN = 3  # consecutive dry minutes before it's a drought


def _farm_droughts(match: dict, player: dict) -> list[dict]:
    """Stretches of _DROUGHT_MIN+ consecutive minutes with almost no last hits,
    labeled with whether the player died or a teamfight ran during the stretch
    and the team's gold state entering it."""
    lh = player.get("lh_t") or []
    death_minutes = {t // 60 for t, _ in _deaths(match, player)}
    fights = [
        (f["start"] // 60, f["end"] // 60)
        for f in match.get("teamfights") or []
        if f.get("start") is not None and f.get("end") is not None
    ]
    droughts = []

    def flush(start: int, end: int):
        if end - start + 1 < _DROUGHT_MIN:
            return
        droughts.append(
            {
                "from_min": start,
                "to_min": end + 1,
                "last_hits_gained": lh[end + 1] - lh[start],
                "died_in_span": any(start <= m <= end + 1 for m in death_minutes),
                "teamfight_in_span": any(
                    s <= end + 1 and e >= start for s, e in fights
                ),
                "team_gold_adv_at_start": _gold_adv_at(match, player, start),
            }
        )

    run = None
    for i in range(len(lh) - 1):
        if lh[i + 1] - lh[i] <= _DROUGHT_LH:
            if run is None:
                run = i
        else:
            if run is not None:
                flush(run, i - 1)
            run = None
    if run is not None:
        flush(run, len(lh) - 2)
    return droughts


def get_farm_pattern(match_id: int, account_id: int | None = None) -> dict:
    """Farming facts for a pos-1 review. cs_checkpoints: the player's last hits
    at the standard checkpoint minutes vs the target band (met = reached the
    low bound). farm_droughts: stretches of 3+ minutes with almost no last
    hits, each labeled with whether the player died or a teamfight ran in the
    stretch and the gold state entering it. camps_stacked from the match.
    farm_gold_by_zone (Stratz deep parse, when available): gold from lane
    creeps vs neutral camps vs ancients vs buildings. Facts only — whether a
    drought was justified (dead map, defending) is not in the data."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    if not _is_parsed(player):
        return {"parsed": False, "note": "match not parsed; farm data unavailable"}
    lh = player.get("lh_t") or []
    checkpoints = []
    for minute, lo, hi in _CS_TARGETS:
        if minute < len(lh):
            checkpoints.append(
                {
                    "minute": minute,
                    "last_hits": lh[minute],
                    "target": f"{lo}-{hi}" if hi else f"{lo}+",
                    "met": lh[minute] >= lo,
                }
            )
    fd = (_stratz_match_stats(match_id).get(player.get("account_id")) or {}).get(
        "farmDistributionReport"
    ) or {}
    zones = None
    if fd:

        def gold_of(k):
            return sum(x.get("gold") or 0 for x in fd.get(k) or [])

        zones = {
            "lane_creeps": gold_of("creepLocation"),
            "neutral_camps": gold_of("neutralLocation"),
            "ancients": gold_of("ancientLocation"),
            "buildings": gold_of("buildings"),
        }
    return {
        "parsed": True,
        "cs_checkpoints": checkpoints,
        "farm_droughts": _farm_droughts(match, player),
        "camps_stacked": player.get("camps_stacked"),
        "farm_gold_by_zone": zones,
    }


# Physical lanes: 1=bot, 2=mid, 3=top, shared by both teams. Which positions a
# lane's duo holds depends on the side: bot is radiant's safelane (core 1 +
# support 5) but dire's offlane (3 + 4), and top the reverse; mid is solo.
# Within a lane the higher-GPM hero is the core.
def _lane_heroes(players: list[dict], lane: int, radiant: bool) -> dict:
    in_lane = sorted(
        (p for p in players if p.get("lane") == lane),
        key=lambda p: p.get("gold_per_min", 0),
        reverse=True,
    )
    if lane == 2:
        positions = ("2", "2")
    elif (lane == 1) == radiant:
        positions = ("1", "5")
    else:
        positions = ("3", "4")
    heroes = {}
    for p, pos in zip(in_lane[:2], positions):
        heroes[pos] = Hero(
            ID_TO_NAME.get(p["hero_id"], str(p["hero_id"])), str(p["hero_id"]), 0, pos
        )
    return heroes


def score_lane_matchup(match_id: int, account_id: int | None = None) -> dict:
    """Score the heroes who shared the player's lane against each other, with
    the same whole-game win-rate/counter math as draft scoring — the heroes'
    game-level matchup, not a laning-phase measure. Positions come from the
    physical lane and side (bot is radiant's safelane, dire's offlane).
    lane_outcome carries what actually happened in lane: last hits and denies
    at 10 minutes and lane efficiency. Positive advantage favors the player's
    side."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    lane = player.get("lane")
    if lane not in (1, 2, 3):
        return {"note": "player's lane is unknown or jungle; cannot score lane"}
    radiant = player["player_slot"] < 128
    allies = [p for p in match["players"] if (p["player_slot"] < 128) == radiant]
    enemies = [p for p in match["players"] if (p["player_slot"] < 128) != radiant]

    ally = _lane_heroes(allies, lane, radiant)
    enemy = _lane_heroes(enemies, lane, not radiant)
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

    def outcome(players):
        out = {}
        for p in players:
            if p.get("lane") != lane:
                continue
            lh = p.get("lh_t")
            dn = p.get("dn_t")
            out[ID_TO_NAME.get(p["hero_id"], str(p["hero_id"]))] = {
                "last_hits_at_10": lh[10] if lh and len(lh) > 10 else None,
                "denies_at_10": dn[10] if dn and len(dn) > 10 else None,
                "lane_efficiency_pct": p.get("lane_efficiency_pct"),
            }
        return out

    return {
        "ally_lane_score": round(ally_score, 1),
        "enemy_lane_score": round(enemy_score, 1),
        "advantage": round(ally_score - enemy_score, 1),
        "breakdown": breakdown,
        "lane_outcome": {
            "ally_lane": outcome(allies),
            "enemy_lane": outcome(enemies),
        },
    }


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

CARRY_SEED = {
    "Yatoro": 321580662,
    "Ame": 898754153,
    "skiter": 100058342,
    "Crystallis": 127617979,
    "Nightfall": 124801257,
    "23savage": 375507918,
}


def _player_completed_items(match_id: int, account_id: int | None) -> dict[str, str]:
    """shortName -> displayName for the player's completed items in this match.
    Union of the purchase log and the final inventory: the log needs a parsed
    replay and can miss items, the slots are always present and hold the actual
    final build (but not items sold or consumed into upgrades mid-game)."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    shorts = [e["key"] for e in player.get("purchase_log") or []]
    slots = [f"item_{i}" for i in range(6)] + [f"backpack_{i}" for i in range(3)]
    shorts += [_item_short(player[s]) for s in slots if player.get(s)]
    items = {}
    for short in shorts:
        v = item_data.get(f"item_{short}")
        if v and v.get("quality") in _COMPLETED_QUALITY:
            items[short] = v["displayName"]
    return items


def _component_closure(shorts: set[str]) -> set[str]:
    """The items plus everything consumed into them, transitively (Hurricane
    Pike closes over Dragon Lance and Force Staff)."""
    items = _get_obj("/constants/items")
    out: set[str] = set()
    stack = list(shorts)
    while stack:
        s = stack.pop()
        if s in out:
            continue
        out.add(s)
        stack.extend((items.get(s) or {}).get("components") or [])
    return out


def _collapse_upgrades(shorts: set[str]) -> set[str]:
    """Drop items that another item in the set builds out of: a build with
    Hurricane Pike should not also feature Dragon Lance and Force Staff."""
    consumed = set()
    for s in shorts:
        consumed |= _component_closure({s}) - {s}
    return shorts - consumed


def get_matchup_builds(
    carry_hero_id: int,
    enemy_hero_id: int,
    match_id: int | None = None,
    account_id: int | None = None,
    n: int = 6,
) -> dict:
    """Recent completed-item builds (item + minute) pro pos-1 carries bought on
    carry_hero_id in games where enemy_hero_id was on the opposing team.
    enemy_hero_id can be a support, which is often the more useful matchup. Only
    completed items are shown (no components, consumables, or recipes), like
    dota2protracker. Each build also carries final: the item slots actually held
    at the end of the game. Pass match_id/account_id to also get the player's
    completed build and pros_bought_player_skipped: the completed items pros
    bought here that the player did not, the basis for itemization advice."""
    cache = _load_cache(MATCHUP_BUILDS_CACHE)
    key = f"{carry_hero_id}-{enemy_hero_id}"
    # v3 entries include the Blink family in purchase lists (Valve quality
    # override) and the larger sample (n=6); older entries refetch once.
    if not utils._FRESH and cache.get(key, {}).get("v") == 3:
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
                    take: 3
                }}) {{
                    id
                    players(steamAccountId: {seed_account}) {{
                        item0Id item1Id item2Id item3Id item4Id item5Id
                        backpack0Id backpack1Id backpack2Id
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
                pro = mm["players"][0]
                purchases = pro["stats"].get("itemPurchases")
                if not purchases:
                    continue
                slot_ids = [pro.get(f"item{i}Id") for i in range(6)]
                slot_ids += [pro.get(f"backpack{i}Id") for i in range(3)]
                builds.append(
                    {
                        "player": name,
                        "match_id": mm["id"],
                        "final": [_item_short(s) for s in slot_ids if s],
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
            "v": 3,
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
        have = _component_closure(set(player_items))
        skipped = [s for s in pro_by_short if _notable(s) and s not in have]
        skipped.sort(key=lambda s: bought_in[s], reverse=True)
        result["player_build"] = sorted(
            player_items[s] for s in _collapse_upgrades(set(player_items))
        )
        result["pros_bought_player_skipped"] = [
            {"item": pro_by_short[s], "pro_builds": bought_in[s]} for s in skipped
        ]
    return result


_CORE_FRAC = 0.5  # a notable item in this share of pro builds is the core build
_ALT_MIN_GAMES = 2  # an alternative item must appear in at least this many builds
_DISTINCT_MAX_OVERLAP = 1  # a build sharing <= this with the core is a distinct build


def get_build_gaps(match_id: int, account_id: int | None = None) -> dict:
    """How pros build the player's hero, and which of those items the player
    skipped. A pro build is the endgame inventory of one pro game, not purchase
    order. Pulls pro builds across every enemy (each is single-hero-conditioned,
    not vs the whole lineup, so this is the general build, not matchup-reactive).
    Reports: core (items in most pro builds), alternatives (situational items that
    recur but are not core), any distinct_build (a genuinely different build that
    shares almost nothing with the core, e.g. a magic vs right-click split),
    player_items (each with its pro-build count, so a rare pick reads as rare,
    plus descriptive tags where item_tags.json has them), player_skipped (items
    from the build the player was on that they did not buy), timing_vs_pros
    (player completion minute vs the pro median), and
    player_items_in_builds_vs_enemy (per enemy in this game, how many of that
    enemy's sampled builds contained each player item — small n, counts only)."""
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    carry_id = player["hero_id"]
    radiant = player["player_slot"] < 128
    enemies = [p for p in match["players"] if (p["player_slot"] < 128) != radiant]

    mine = _player_completed_items(match_id, account_id)
    player_items = _collapse_upgrades({s for s in mine if _notable(s)})

    seen = set()
    pro_builds: list[set[str]] = []  # each build's notable completed item shorts
    name_of: dict[str, str] = {}
    pro_minutes: dict[str, list[int]] = {}  # first completion minute per build
    conditioned: dict[str, dict] = {}  # per enemy: player items in their builds
    # Purchases count too: consumed items (Aghanim's Shard) never sit in the
    # final inventory but were bought all the same.
    bought_in: Counter = Counter()
    for e in enemies:
        result = get_matchup_builds(
            carry_id, e["hero_id"], match_id=match_id, account_id=account_id
        )
        ebuilds = list({b["match_id"]: b for b in result["builds"]}.values())
        if ebuilds and player_items:
            counts = {}
            for s in player_items:
                c = sum(
                    1
                    for b in ebuilds
                    if s in set(b.get("final") or [i["short"] for i in b["items"]])
                )
                counts[mine[s]] = c
            conditioned[result["vs"]] = {"builds": len(ebuilds), "player_items": counts}
        for b in result["builds"]:
            if b["match_id"] in seen:
                continue  # same pro game can surface under multiple enemies
            seen.add(b["match_id"])
            shorts = b.get("final") or [i["short"] for i in b["items"]]
            items = _collapse_upgrades({s for s in shorts if _notable(s)})
            if items:
                pro_builds.append(items)
            firsts: dict[str, int] = {}
            for i in b["items"]:
                name_of[i["short"]] = i["item"]
                firsts.setdefault(i["short"], i["minute"])
            for s, m in firsts.items():
                pro_minutes.setdefault(s, []).append(m)
            bought_in.update(set(shorts) | set(firsts))
            for s in b.get("final") or []:
                name_of.setdefault(
                    s, (item_data.get(f"item_{s}") or {}).get("displayName", s)
                )

    n = len(pro_builds)
    if not n:
        return {"carry": ID_TO_NAME.get(carry_id, str(carry_id)), "core": []}

    freq = Counter(s for b in pro_builds for s in b)
    core = {s for s, c in freq.items() if c >= n * _CORE_FRAC}
    alts = {s for s, c in freq.items() if s not in core and c >= _ALT_MIN_GAMES}

    # A distinct build is a pulled build that barely overlaps the core and adds
    # items of its own (a real alternative like a magic build vs a right-click
    # core), if one exists.
    distinct = set()
    for b in pro_builds:
        shared_with_core = b.intersection(core)
        own_items = b.difference(core)
        if len(shared_with_core) <= _DISTINCT_MAX_OVERLAP and own_items:
            distinct = b
            break

    # Diff against the build the player was actually going for, not a global pool:
    # if a distinct build exists and the player's items match it more than the
    # core, compare to it; otherwise compare to the core. alternatives are
    # informational only and never count as a skipped gap.
    target = core
    on_distinct = False
    if distinct and len(player_items.intersection(distinct)) > len(
        player_items.intersection(core)
    ):
        target = distinct
        on_distinct = True
    # Closure so an upgrade covers what it consumed: owning Silver Edge is not
    # skipping Shadow Blade.
    skipped = target.difference(_component_closure(player_items))

    def names(shorts):
        return [name_of[s] for s in sorted(shorts, key=lambda s: -freq[s])]

    purchases = {e["key"]: e["time"] for e in player.get("purchase_log") or []}
    annotated = []
    timing = []
    for s in sorted(player_items, key=lambda s: -bought_in.get(s, 0)):
        entry = {"item": mine[s], "pro_builds": bought_in.get(s, 0), "of": n}
        tags = [k for k, v in (item_tags.get(s) or {}).get("tags", {}).items() if v == 2]
        if tags:
            entry["tags"] = tags
        annotated.append(entry)
        mins = pro_minutes.get(s)
        if mins and len(mins) >= 2 and s in purchases:
            timing.append(
                {
                    "item": mine[s],
                    "player_min": round(purchases[s] / 60, 1),
                    "pro_median_min": round(median(mins), 1),
                    "pro_builds": len(mins),
                }
            )

    out = {
        "carry": ID_TO_NAME.get(carry_id, str(carry_id)),
        "pro_builds_sampled": n,
        "core": names(core),
        "alternatives": names(alts),
        "player_items": annotated,
        "player_build": "distinct" if on_distinct else "core",
        "player_skipped": names(skipped),
        "timing_vs_pros": timing,
        "player_items_in_builds_vs_enemy": conditioned,
    }
    if distinct:
        out["distinct_build"] = names(distinct)
    return out


def _break_dispel_lists(enemy_hero_ids: list[int]) -> tuple[dict, dict]:
    """Per enemy hero, the ability names Silver Edge break disables (passives)
    and a basic dispel like Nullifier removes, from hero_break_dispel.json
    (generated per patch by gather_data from Valve ability constants)."""
    breakable: dict[str, list[str]] = {}
    dispellable: dict[str, list[str]] = {}
    for hero_id in enemy_hero_ids:
        entry = hero_break_dispel.get(str(hero_id)) or {}
        if entry.get("breakable_passives"):
            breakable[entry["name"]] = entry["breakable_passives"]
        if entry.get("basic_dispellable"):
            dispellable[entry["name"]] = entry["basic_dispellable"]
    return breakable, dispellable


def get_break_dispel_targets(match_id: int, account_id: int | None = None) -> dict:
    """What Silver Edge or Nullifier would act on in this game, from Valve
    ability and item data: each enemy's passive abilities (break disables
    passives), basic-dispellable buffs (Nullifier repeatedly basic-dispels its
    target), and the dispellable save items they actually carried this game
    (Ghost Scepter, Glimmer Cape, ...), plus how many of the pro builds sampled
    for this matchup carried each item. The names and counts are facts; whether
    the item was worth buying is for the player to weigh."""
    if not hero_break_dispel:
        return {"error": "hero_break_dispel.json missing; run scripts/gather_data.py"}
    match = _get_obj(f"/matches/{match_id}")
    player = _find_player(match, account_id) or match["players"][0]
    radiant = player["player_slot"] < 128
    enemies = [p for p in match["players"] if (p["player_slot"] < 128) != radiant]
    breakable, dispellable = _break_dispel_lists([e["hero_id"] for e in enemies])

    slots = [f"item_{i}" for i in range(6)] + [f"backpack_{i}" for i in range(3)]
    dispellable_items = {}
    for e in enemies:
        carried = []
        for s in slots:
            if not e.get(s):
                continue
            v = item_data.get(f"item_{_item_short(e[s])}") or {}
            if v.get("basic_dispellable") and v["displayName"] not in carried:
                carried.append(v["displayName"])
        if carried:
            dispellable_items[ID_TO_NAME.get(e["hero_id"], str(e["hero_id"]))] = carried

    seen = set()
    with_se = with_null = 0
    for e in enemies:
        for b in get_matchup_builds(player["hero_id"], e["hero_id"])["builds"]:
            if b["match_id"] in seen:
                continue
            seen.add(b["match_id"])
            shorts = set(b.get("final") or [i["short"] for i in b["items"]])
            with_se += "silver_edge" in shorts
            with_null += "nullifier" in shorts

    return {
        "enemy_breakable_passives": breakable,
        "enemy_basic_dispellable": dispellable,
        "enemy_dispellable_items_carried": dispellable_items,
        "pro_usage": {
            "pro_builds_sampled": len(seen),
            "sampling": "each sampled build faced one of these enemies, not the full lineup",
            "builds_with_silver_edge": with_se,
            "builds_with_nullifier": with_null,
        },
    }


_TREND_METRICS = {
    "gold_per_min": "gold_per_min",
    "last_hits": "last_hits",
    "hero_damage": "hero_damage",
    "kills": "kills",
    "deaths": "deaths",
    "assists": "assists",
}


def get_metric_trend(
    account_id: int, hero_id: int, metric: str, limit: int = 20
) -> dict:
    """The player's recent distribution of a metric on one hero (count, average,
    min, max), to tell whether one game's value is typical for them. metric is one
    of gold_per_min, last_hits, hero_damage, kills, deaths, assists."""
    field = _TREND_METRICS.get(metric)
    if field is None:
        return {"error": f"metric must be one of {sorted(_TREND_METRICS)}"}
    matches = _get_list(
        f"/players/{account_id}/matches",
        {"hero_id": hero_id, "limit": limit, "project": [field]},
    )
    values = [m[field] for m in matches if m.get(field) is not None]
    if not values:
        return {
            "hero": ID_TO_NAME.get(hero_id, str(hero_id)),
            "metric": metric,
            "games": 0,
        }
    return {
        "hero": ID_TO_NAME.get(hero_id, str(hero_id)),
        "metric": metric,
        "games": len(values),
        "average": round(sum(values) / len(values), 1),
        "min": min(values),
        "max": max(values),
    }


def get_patch_notes(hero_id: int) -> dict:
    """Recent patch changes for a hero: base-stat changes and per-ability changes,
    keyed by patch version."""
    entry = patch_data["heroes"].get(str(hero_id)) or {}
    abilities = {
        ability: changes
        for ability, changes in (entry.get("abilities") or {}).items()
        if changes
    }
    return {
        "hero": ID_TO_NAME.get(hero_id, str(hero_id)),
        "hero_changes": entry.get("hero") or {},
        "ability_changes": abilities,
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
        "description": "Core facts for one match: hero, lane role, KDA, GPM/XPM, win/loss, the player's side, ally_heroes and enemy_heroes (from the player's perspective), and whether the match is parsed. largest_team_lead and largest_team_deficit say whether the game was one-sided or swung: a win with a real deficit was a comeback, a loss with a real lead was thrown. final_networth_rank is the endgame scoreboard rank among all ten heroes. Call this before the analysis tools; its hero_id and enemy_heroes feed them.",
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
        "description": "Performance percentiles for the player's hero in this match (GPM/XPM/last-hits/damage/tower damage vs OpenDota benchmarks), teamfight_participation_pct (share of the team's kills the player took part in), and a list of weak_areas. Use to decide what to investigate next.",
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
        "description": "Kills and deaths for the player, broken down by game phase (laning <=10min, mid 10-25, late >25) and by opposing hero, plus deaths_detail: each death's context (caught_alone, skirmish, teamfight with dying-first flagged), the player's net-worth rank among all ten heroes at that minute (as 'N of 10' — it holds at that minute only, never a team rank or a trajectory), team gold advantage at the time, and whether they bought back. Also gold_swings: every turning point of the team gold advantage as one chronological 'minute: value' line ending at the final state — base any swing/collapse/comeback claim on it and never skip points (consecutive points reverse direction, so a span between distant points seesawed). enemy_buildings_killed (towers/rax the player last-hit, with minutes) and tower_damage_by_phase: split-pushing evidence to read next to gold_swings. buybacks: minutes the player bought back. objective_timeline: Roshan/tormentor kills and aegis pickups with sides — place gold swings next to the objectives that dates them.",
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
        "description": "For each fight-enabling item the player completed (Blink, BKB, Manta, etc), what the 3 minutes after held: kills, deaths, teamfight damage, last hits farmed, whether the team fought without the player, enemy buildings taken and tower damage dealt in the window (split-pushing, not pure farming), and deaths_before_completion: the minutes the player died between 10:00 and finishing the item — fighting before the timing was online. contestable is False when the team was already decided behind, so a late item is not a missed window. Use to spot a strong item timing the player did not turn into a fight, or deaths taken before the kit was ready. Reports facts only, not intent.",
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
        "name": "get_farm_pattern",
        "description": "Farming facts for a pos-1 review. cs_checkpoints: last hits at 10/15/20/25/30/40 minutes vs the standard pos-1 target bands, with met flags — a missed checkpoint is a fact to name. farm_droughts: stretches of 3+ minutes with almost no last hits, labeled with whether the player died or a teamfight ran during the stretch and the gold state entering it — dead time made visible; a drought overlapping a fight is a map question to raise, not an answer. camps_stacked, and farm_gold_by_zone (lane creeps / neutral camps / ancients / buildings, when the deep parse exists). Report the numbers; do not infer movement or decision quality from them.",
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
        "description": "The heroes who shared the player's lane, scored against each other with whole-game win-rate/counter math. advantage is the heroes' game-level matchup, NOT a laning-phase measure — call it a hero matchup, never a lane advantage or a read on the lane itself. How the lane actually went is lane_outcome: each lane hero's last hits at 10 minutes and lane efficiency percent; judge the laning from those numbers. Positive advantage = the player's heroes favored across a whole game.",
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
        "description": "How pros build the player's hero and how the player's build compares, with counts as the judgment. player_items each carry pro_builds/of — how many sampled pro builds contained the item; 0 of 18 is a fact worth naming, and an item is never 'a fine choice' on your say-so. timing_vs_pros compares the player's completion minute to the pro median. player_items_in_builds_vs_enemy gives, per enemy actually in this game, how many builds sampled against that enemy contained each player item — n is small, so state counts ('3 of 6 builds with Medusa'), never percentages. core/alternatives/distinct_build describe the pro build shape; player_skipped are items from the build the player was on that they did not buy. Item tags, where present, are descriptive labels derived per patch from Valve item text — state them, do not turn them into advice.",
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
        "name": "get_break_dispel_targets",
        "description": "What Silver Edge or Nullifier would act on in this game, from Valve data: each enemy hero's breakable passives (Silver Edge's break disables passives), basic-dispellable buffs (Nullifier repeatedly basic-dispels its target), and the dispellable save items each enemy actually carried this game (Ghost Scepter, Glimmer Cape, ...). pro_usage counts sampled pro builds carrying each item; every sampled build faced ONE of these enemies, so say 'in pro games featuring one of these enemies', never 'against this lineup'. Use for any 'should I have bought Silver Edge / Nullifier' question; state only the listed names and counts — add no effect or matchup explanation of your own.",
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
        "name": "get_metric_trend",
        "description": "The player's recent average/min/max of a metric on one hero across their last games, to judge whether this match's value is typical for them (e.g. 'is my farm always this low'). metric is one of gold_per_min, last_hits, hero_damage, kills, deaths, assists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "integer"},
                "hero_id": {"type": "integer"},
                "metric": {"type": "string"},
            },
            "required": ["account_id", "hero_id", "metric"],
        },
    },
    {
        "name": "get_patch_notes",
        "description": "Recent patch changes for a hero: base-stat changes and per-ability changes by patch version. Use when a recent buff or nerf is relevant to the advice.",
        "input_schema": {
            "type": "object",
            "properties": {"hero_id": {"type": "integer"}},
            "required": ["hero_id"],
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
    "get_farm_pattern": get_farm_pattern,
    "score_lane_matchup": score_lane_matchup,
    "get_draft_advantage": get_draft_advantage,
    "get_build_gaps": get_build_gaps,
    "get_break_dispel_targets": get_break_dispel_targets,
    "get_metric_trend": get_metric_trend,
    "get_patch_notes": get_patch_notes,
}
