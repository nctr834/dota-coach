import json
import anthropic
import os
import time
from collections.abc import Iterable
from rapidfuzz import process, fuzz
from json_repair import repair_json
from dotenv import load_dotenv
from evaluator import rank_picks
from counters import get_counter_score, get_synergy_score
from hero_lookup import hero_data, NAME_TO_ID
from data_loader import (
    matchup_data,
    pos_data,
    item_data,
    item_displayName_to_id as item_displayName_to_name,
    patch_data,
    aghs_data,
    hero_tags,
    rag_items,
    hero_item_builds,
)

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

ITEM_NAMES = {item["displayName"] for item in item_data.values()}
RAG_ITEM_NAMES = list(rag_items.keys())
NUM_PICKS = 10

STRATEGIC_TAGS = [
    "stun",
    "root",
    "silence",
    "slow",
    "displacement",
    "initiation",
    "save",
    "heal",
    "burst_damage",
    "sustained_damage",
    "waveclear",
    "mobility",
]

SYSTEM_GAMEPLAY = [
    {
        "type": "text",
        "text": """You are a high-MMR Dota 2 coach on patch 7.40. Be CONCISE — 3-5 short paragraphs max.

Your job is to convert structured match context into short, accurate gameplay advice.

Rules:
- Do not invent mechanics or items not present in the context.
- Prefer statistical signals over assumptions.
- Prioritize immediate gameplay decisions over theory.
- Advice must be concise and actionable.
- If context is insufficient, say "insufficient context".""",
    }
]

SYSTEM_DRAFT = [
    {
        "type": "text",
        "text": """You are a high-MMR Dota 2 draft coach on patch 7.40. Be CONCISE — 2-3 short paragraphs max.

RULES:
- You are given ranked candidate picks with scores. Recommend 2-3 picks and briefly explain WHY.
- Consider: team synergy, enemy counters, lane matchups, what the team lacks (initiation/save/waveclear/lockdown).
- Rely ONLY on the provided data for abilities. If unsure, say so.

CURRENT DRAFT META (7.40):
- Flex picks (Tiny, Pudge, Mirana) are premium — multiple role options give draft edge.
- Offlane heroes dominate: Tidehunter (auto Counter Helix via talent), Tiny (Avalanche aura stun).
- Illusion carries (PL, Naga) are weak — Diffusal doesn't work on illusions, Radiance blind removed.
- Scaling carries with high-ground presence favored (Juggernaut, Drow Ranger).
- High-ground defense is very strong (T4 tower armor buff) — draft for multi-lane pressure, not single-lane deathball.
- Captain's Mode ban order changed: first team gets back-to-back bans in phase 1.

ROLES: Pos 1 = carry, 2 = mid, 3 = offlane, 4 = soft support, 5 = hard support. Pos 1+5 lane vs 3+4.""",
    }
]


def _get_response(
    prompt: str,
    tokens: int = 1024,
    system: list = [],
    model: str = "claude-haiku-4-5-20251001",
    retries: int = 3,
) -> str:
    for attempt in range(retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            block = response.content[0]
            return block.text.strip() if block.type == "text" else ""
        except (anthropic.InternalServerError, anthropic.RateLimitError):
            if attempt < retries - 1:
                time.sleep(2**attempt)
            else:
                raise
    raise RuntimeError("unreachable: retries exhausted without return or raise")


def _parse_json_response(response: str) -> dict:
    try:
        return json.loads(repair_json(response))
    except Exception:
        print("Failed to parse response as JSON")
        return {}


def _verify_names(names: list[str], valid_names: Iterable[str]) -> list[str]:
    matches = []
    for name in names:
        name = name.title()
        match = process.extractOne(
            name,
            list(valid_names),
            scorer=fuzz.ratio,
            score_cutoff=70,
        )
        if match:
            matches.append(match[0])
    return matches


def _dedup(values: list[str]) -> list[str]:
    seen = set()
    return [v for v in values if not (v in seen or seen.add(v))]


def _hero_display_name(hero_id: str) -> str:
    return hero_data.get(hero_id, {}).get("displayName", f"Hero#{hero_id}")


def _get_hero_tags(hero_id: str) -> dict:
    """Return tag dict for a hero, or empty dict if unavailable."""
    entry = hero_tags.get(str(hero_id), {})
    return entry.get("tags", {})


def _format_hero_tags_line(hero_id: str, name: str) -> str:
    """One-line tag summary for a hero: 'Name: stun=2, slow=1, ...' (non-zero only)."""
    tags = _get_hero_tags(hero_id)
    if not tags:
        return ""
    non_zero = [f"{k}={v}" for k, v in tags.items() if v > 0]
    return f"  {name}: {', '.join(non_zero)}" if non_zero else ""


def _sum_team_tags(team: dict) -> dict:
    """Sum strategic tags across all picked heroes in a team."""
    totals = {t: 0 for t in STRATEGIC_TAGS}
    for hero in team.values():
        if not hero.id:
            continue
        tags = _get_hero_tags(hero.id)
        for k in STRATEGIC_TAGS:
            totals[k] += tags.get(k, 0)
    return totals


def _build_tag_analysis(team: dict, enemy_team: dict) -> str:
    """Build team capability analysis from summed strategic tags."""
    if not hero_tags:
        return ""
    team_totals = _sum_team_tags(team)
    enemy_totals = _sum_team_tags(enemy_team)

    has_team = any(h.id for h in team.values())
    has_enemy = any(h.id for h in enemy_team.values())
    if not has_team and not has_enemy:
        return ""

    parts = []
    if has_team:
        team_str = " ".join(f"{k}={v}" for k, v in team_totals.items() if v > 0)
        parts.append(f"TEAM TAGS [{team_str}]")
        gaps = [k for k in STRATEGIC_TAGS if team_totals[k] == 0]
        if gaps:
            parts.append(f"GAPS: {', '.join(gaps)}")
    if has_enemy:
        enemy_str = " ".join(f"{k}={v}" for k, v in enemy_totals.items() if v > 0)
        parts.append(f"ENEMY TAGS [{enemy_str}]")
        enemy_gaps = [k for k in STRATEGIC_TAGS if enemy_totals[k] == 0]
        if enemy_gaps:
            parts.append(f"ENEMY GAPS: {', '.join(enemy_gaps)}")
    return "\n" + "\n".join(parts) + "\n"


def extract_from(query: str) -> dict:
    prompt = f"""Extract hero and item names from this Dota 2 query. Return ONLY a JSON object with:
- "heroes": array of FULL hero names mentioned
- "items": array of FULL item names mentioned

Query: {query}"""
    result = _parse_json_response(_get_response(prompt))
    result["heroes"] = _dedup(
        _verify_names(result.get("heroes", []), NAME_TO_ID.keys())
    )
    result["items"] = _dedup(_verify_names(result.get("items", []), ITEM_NAMES))
    return result


def _get_enemy_pick(enemy_team: dict, pos: str) -> str:
    """Get the display name of the enemy hero in the same position."""
    try:
        hero = enemy_team.get(int(pos))
        if hero and hero.id:
            return _hero_display_name(hero.id)
    except (KeyError, ValueError):
        pass
    return ""


def _get_aghs_data(hero_name: str) -> str:
    """Return formatted Aghanim's Scepter/Shard info for a hero."""
    hero_id = NAME_TO_ID.get(hero_name)
    if not hero_id:
        return ""
    entry = aghs_data.get(hero_id, {})
    parts = []
    if entry.get("has_scepter") and entry.get("scepter_desc"):
        skill = entry.get("scepter_skill_name", "")
        label = f"Scepter ({skill})" if skill else "Scepter"
        parts.append(f"  {label}: {entry['scepter_desc']}")
    if entry.get("has_shard") and entry.get("shard_desc"):
        skill = entry.get("shard_skill_name", "")
        label = f"Shard ({skill})" if skill else "Shard"
        parts.append(f"  {label}: {entry['shard_desc']}")
    return "\n".join(parts)


def _get_patch_notes(key: str, path: str = "") -> str:
    """Return formatted recent patch notes for a hero or item."""
    if path:
        notes = patch_data["heroes"][str(key)][path]
    else:
        notes = patch_data["items"][str(key)]
    if not notes:
        return ""
    parts = []
    for patch, changes in notes.items():
        if changes:
            parts.append(f"  {patch}: {changes}")
    return "\n".join(parts)


def _format_item_builds(hero_id: str, hero_name: str) -> str:
    """Format popular item builds for a hero from pro game data."""
    builds = hero_item_builds.get(str(hero_id))
    if not builds:
        return ""
    parts = [f"{hero_name}'s Popular Items (pro games):"]
    phase_labels = {
        "start_game": "Starting",
        "early_game": "Early",
        "mid_game": "Mid",
        "late_game": "Late",
    }
    for phase_key, label in phase_labels.items():
        phase = builds.get(phase_key)
        if not phase or not phase.get("items"):
            continue
        item_strs = [f"{it['name']}({it['rate']}%)" for it in phase["items"]]
        parts.append(f"  {label}: {', '.join(item_strs)}")
    return "\n".join(parts) + "\n"


def _lookup_rag_item(display_name: str) -> dict | None:
    """Look up an item in rag_items by exact match, then fuzzy match."""
    if display_name in rag_items:
        return rag_items[display_name]
    match = process.extractOne(
        display_name,
        RAG_ITEM_NAMES,
        scorer=fuzz.ratio,
        score_cutoff=70,
    )
    if match:
        return rag_items[match[0]]
    return None


def _format_rag_item(display_name: str) -> str:
    """Format an item using RAG data: compact one-liner + patch notes."""
    rag = _lookup_rag_item(display_name)
    item_key = item_displayName_to_name.get(display_name)
    if not rag:
        # Fallback: just name + cost from item_data
        if item_key and item_key in item_data:
            cost = item_data[item_key].get("cost", "?")
            return f"{display_name} ({cost}g)"
        return display_name

    cost = rag.get("cost", "?")
    provides = rag.get("provides", "")
    buy = rag.get("buyWhen", "")
    skip = rag.get("skipWhen", "")
    line = f"{display_name} ({cost}g): {provides}"
    if buy:
        line += f" | buy: {buy}"
    if skip:
        line += f" | skip: {skip}"
    # Append patch notes from RAG if available
    patch = rag.get("patchNotes", "")
    if patch:
        line += f"\n  patch: {patch}"
    elif item_key:
        try:
            patch_info = _get_patch_notes(item_key)
            if patch_info:
                line += f"\n{patch_info}"
        except KeyError:
            pass
    return line


def _add_item_context(header, items):
    item_context = ""
    if items:
        item_context += f"{header}:\n"
        for display_name in items:
            item_context += f"  {_format_rag_item(display_name)}\n"
    return item_context


def _format_other_hero(hero_id: str, name: str) -> str:
    """Compact format for mentioned heroes: tags + patch notes only."""
    tags = _get_hero_tags(hero_id)
    non_zero = ",".join(f"{k}={v}" for k, v in tags.items() if v > 0)
    line = f"{name}: {non_zero}" if non_zero else name
    # Add patch notes
    try:
        patch_info = _get_patch_notes(hero_id, "hero")
        if patch_info:
            line += f"\n{patch_info}"
    except KeyError:
        pass
    return line


def build_gameplay_context(
    pick: str = "",
    enemy_pick: str = "",
    extracted: dict = {},
) -> str:
    context = ""

    # User's hero data + tags + aghs + patch notes
    if pick:
        hero_id = NAME_TO_ID[pick]
        hero = hero_data.get(hero_id, {})
        # Hero tags (compact capability summary)
        tags_line = _format_hero_tags_line(hero_id, pick)
        if tags_line:
            context += f"{pick}'s Capabilities:\n{tags_line}\n"
        # Hero abilities context (full detail for user's hero)
        context += f"{pick}'s Abilities:\n"
        for ab in hero.get("abilities", []):
            context += f"  {ab['displayName']}: {ab.get('description', '')}\n"
        # Tips
        if hero.get("tips"):
            context += f"{pick}'s Tips:\n"
            for tip in hero["tips"]:
                context += f"  - {tip}\n"
        aghs_info = _get_aghs_data(pick)
        if aghs_info:
            context += f"{pick}'s Aghanim's Scepter/Shard Data:\n{aghs_info}\n"
        try:
            patch_info = f"{_get_patch_notes(hero_id, 'hero')}\n{_get_patch_notes(hero_id, 'abilities')}"
            if patch_info.strip():
                context += f"{pick}'s Recent Patch Notes:\n{patch_info}\n"
        except KeyError:
            pass
        # Popular item builds from pro games
        builds_info = _format_item_builds(hero_id, pick)
        if builds_info:
            context += builds_info

    # Enemy hero: tags + ability names + hero & ability patch notes
    if enemy_pick:
        enemy_hero_id = NAME_TO_ID[enemy_pick]
        enemy_hero = hero_data.get(enemy_hero_id, {})
        tags_line = _format_hero_tags_line(enemy_hero_id, enemy_pick)
        if tags_line:
            context += f"{enemy_pick}'s Capabilities:\n{tags_line}\n"
        ability_names = [ab["displayName"] for ab in enemy_hero.get("abilities", [])]
        context += f"{enemy_pick}'s Abilities: {', '.join(ability_names)}\n"
        try:
            patch_parts = []
            hero_patch = _get_patch_notes(enemy_hero_id, "hero")
            if hero_patch:
                patch_parts.append(hero_patch)
            ability_patch = _get_patch_notes(enemy_hero_id, "abilities")
            if ability_patch:
                patch_parts.append(ability_patch)
            if patch_parts:
                context += (
                    f"{enemy_pick}'s Recent Patch Notes:\n"
                    + "\n".join(patch_parts)
                    + "\n"
                )
        except KeyError:
            pass

    # Other heroes mentioned in query — compact format
    extracted_heroes = extracted.get("heroes", [])
    extracted_items = extracted.get("items", [])

    other_heroes = [h for h in extracted_heroes if h not in (pick, enemy_pick)]
    if other_heroes:
        context += "Other Heroes:\n"
        for hero in other_heroes:
            hero_id = NAME_TO_ID.get(hero)
            if hero_id:
                context += f"  {_format_other_hero(hero_id, hero)}\n"

    # Items mentioned in query — RAG format
    context += _add_item_context("Item Data", extracted_items)

    return context


def build_draft_context(
    team: dict = {},
    enemy_team: dict = {},
    pos: str = "",
    side: str = "",
) -> str:
    ranked_picks = rank_picks(team, enemy_team, pos)[:NUM_PICKS]

    context = f"\nDRAFT pos{pos} — Top {NUM_PICKS}:\n"
    for hero in ranked_picks:
        name = _hero_display_name(hero.id)
        # Compute score breakdown
        breakdown_parts = []
        # Base WR
        hero_pos = pos_data.get(str(pos), {}).get(hero.id, {})
        if hero_pos and hero_pos.get("matchCount", 0) > 0:
            base_wr = 100 * hero_pos["winCount"] / hero_pos["matchCount"]
            breakdown_parts.append(f"WR:{base_wr:.1f}%")
        # Counter scores vs each enemy
        vs_parts = []
        for enemy in enemy_team.values():
            if not enemy.id:
                continue
            try:
                cs = get_counter_score(
                    hero.id, enemy.id, matchup_data, str(pos), enemy.pos, pos_data
                )
                if abs(cs) >= 0.1:
                    enemy_name = _hero_display_name(enemy.id)
                    vs_parts.append(f"{enemy_name}{cs:+.1f}")
            except (KeyError, ZeroDivisionError):
                continue
        if vs_parts:
            breakdown_parts.append(f"vs:[{','.join(vs_parts)}]")
        # Synergy scores with each ally
        syn_parts = []
        for ally in team.values():
            if not ally.id:
                continue
            try:
                ss = get_synergy_score(
                    hero.id, ally.id, matchup_data, str(pos), ally.pos, pos_data
                )
                if abs(ss) >= 0.1:
                    ally_name = _hero_display_name(ally.id)
                    syn_parts.append(f"{ally_name}{ss:+.1f}")
            except (KeyError, ZeroDivisionError):
                continue
        if syn_parts:
            breakdown_parts.append(f"syn:[{','.join(syn_parts)}]")

        breakdown = f" ({' '.join(breakdown_parts)})" if breakdown_parts else ""
        context += f"  {name}: {hero.score:.1f}{breakdown}\n"

    # Team capability analysis from tags
    context += _build_tag_analysis(team, enemy_team)

    return context


def build_team_context(
    team: dict = {},
    enemy_team: dict = {},
    side: str = "",
    pick: str = "",
    pos: str = "",
) -> str:
    context = "\nTEAM CONTEXT:\n"
    if pick:
        context += f"User picked: {pick}\n"
    if pos:
        context += f"User role: Position {pos}\n"
    if side:
        context += f"User side: {side}\n"

    team_label = "Radiant" if side == "radiant" else "Dire"
    enemy_label = "Dire" if side == "radiant" else "Radiant"

    if team_label == "Radiant":
        names = [
            f"{_hero_display_name(h.id)} (Pos {h.pos})" for h in team.values() if h.id
        ]
        if names:
            context += f"{team_label}: {', '.join(names)}\n"
    if team_label == "Dire":
        names = [
            f"{_hero_display_name(h.id)} (Pos {h.pos})"
            for h in enemy_team.values()
            if h.id
        ]
        if names:
            context += f"{enemy_label}: {', '.join(names)}\n"

    return context


def generate_response(
    query: str,
    team: dict = {},
    enemy_team: dict = {},
    pick: str = "",
    pos: str = "",
    side: str = "",
) -> str:
    context = f"QUERY: {query}\n"
    system = SYSTEM_GAMEPLAY

    is_draft = bool(pos) and not bool(pick)
    enemy_pick = _get_enemy_pick(enemy_team, pos) if pos else ""

    if is_draft:
        context += build_draft_context(team, enemy_team, pos, side)
        system = SYSTEM_DRAFT
    elif pick:
        extracted = extract_from(query)
        context += build_gameplay_context(pick, enemy_pick, extracted)
        system = SYSTEM_GAMEPLAY

    context += build_team_context(team, enemy_team, side, pick, pos)
    prompt = f"{context}\nResponse:"
    print(prompt)
    resp = _get_response(prompt, system=system)
    print(f"\n\n{resp}")
    return resp
