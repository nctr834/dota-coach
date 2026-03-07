import json
import anthropic
import os
import time
from rapidfuzz import process, fuzz
from json_repair import repair_json
from dotenv import load_dotenv
from evaluator import rank_picks
from requests import get

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)
with open("data/hero_displayName_to_id.json", "r") as f:
    hero_displayName_to_id = json.load(f)
with open("data/item_data.json", "r") as f:
    item_data = json.load(f)
with open("data/aghs_data.json", "r") as f:
    aghs_data = json.load(f)
with open("data/patch_data.json", "r") as f:
    patch_data = json.load(f)
# TODO: re-enable once hero guides are regenerated via scripts/generate_hero_guides.py
# with open("data/RAG/RAG_content_heroes.json", "r") as f:
#     hero_guides = json.load(f)

HERO_NAMES = {hero["displayName"]: hero["id"] for hero in hero_data.values()}
ITEM_NAMES = {item["displayName"] for item in item_data.values()}
NUM_PICKS = 10

SYSTEM_GAMEPLAY = [
    {
        "type": "text",
        "text": """You are a high-MMR Dota 2 coach on patch 7.40. Be CONCISE — 3-5 short paragraphs max.

RULES:
- Short, direct sentences. No filler, no repeating the question.
- Be opinionated — give ONE clear recommendation, then briefly note alternatives.
- Rely ONLY on the provided data for ability/item mechanics. If unsure, say so.

CURRENT META (7.40):
- Games are slower midgame; high-ground defense is very strong (T4 towers gain +4 armor per standing barracks, up to +24).
- Multi-lane pressure required before throne push. Secure Mega Creeps first.
- Illusion heroes nerfed hard: reduced vision, Diffusal/Disperser no longer works on illusions, Radiance blind removed (now 25% evasion).
- Roshan harder to solo (lifesteal reduced 40% physical, 80% spell). Roshan moves between top/bottom pit on day/night cycle starting at 15:00.
- Tormentor spawns at 20:00, alternates sides opposite to Roshan. Grants 250 gold per team member + Aghs Shard.
- Buyback cost: 100 + NetWorth/13, no post-buyback gold penalty.
- Talents no longer cost skill points (separate talent points at 10/15/20/25/27-30).
- Flex picks (Tiny, Pudge) are premium. Offlane heroes that convert survivability into retaliation dominate.
- Heart of Tarrasque scales regen with missing HP. Hand of Midas gives zero XP now (pure gold acceleration).

GAME PHASES:
- Early (0-15m): Win lanes, secure last hits, stack camps. Flagbearer creeps give bonus gold in 1500 radius.
- Mid (15-30m): Take towers, smoke gank, contest Roshan/Tormentor. Wisdom Shrines activate every 7 min.
- Late (30m+): Group for objectives, multi-lane pressure, disciplined fights. Lotus Pools spawn Great Lotuses after T4 neutrals.

ROLES: Pos 1 = carry, 2 = mid, 3 = offlane, 4 = soft support, 5 = hard support. Pos 1+5 lane vs 3+4.""",
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
            return response.content[0].text.strip()
        except (anthropic.InternalServerError, anthropic.RateLimitError):
            if attempt < retries - 1:
                time.sleep(2**attempt)
            else:
                raise


def _parse_json_response(response: str) -> dict:
    try:
        return json.loads(repair_json(response))
    except Exception:
        print("Failed to parse response as JSON")
        return {}


def _verify_names(names: list[str], valid_names: set[str]) -> list[str]:
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


## TODO: re-enable once hero guides are regenerated via scripts/generate_hero_guides.py
# def _get_hero_guide(name: str) -> str:
#     guide = hero_guides.get(name)
#     if not guide:
#         return ""
#     parts = [f"Hero Guide — {name}"]
#     for field in ["position", "goal", "requires", "fights", "dont", "timings", "gameStage"]:
#         if guide.get(field):
#             parts.append(f"  {field}: {guide[field]}")
#     return "\n".join(parts)


def extract_from(query: str, pick: str = "", enemy_pick: str = "") -> dict:
    prompt = f"""Extract hero and item names from this Dota 2 query. Return ONLY a JSON object with:
- "heroes": array of FULL hero names mentioned
- "items": array of FULL item names mentioned
- "itemSuggestions": array of FULL item names of what you would think to suggest for BOTH!: USER: {pick} and ENEMY: {enemy_pick}

Query: {query}"""
    result = _parse_json_response(_get_response(prompt))
    result["heroes"] = _dedup(
        _verify_names(result.get("heroes", []), HERO_NAMES.keys())
    )
    result["items"] = _dedup(_verify_names(result.get("items", []), ITEM_NAMES))
    result["itemSuggestions"] = _dedup(
        _verify_names(result.get("itemSuggestions", []), ITEM_NAMES)
    )
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
    hero_id = HERO_NAMES.get(hero_name)
    if not hero_id:
        return ""
    data = aghs_data.get(hero_id)
    if not data:
        return ""
    parts = []
    if data.get("has_scepter"):
        parts.append(
            f"  Scepter ({data.get('scepter_skill_name', '')}): {data.get('scepter_desc', '')}"
        )
    if data.get("has_shard"):
        parts.append(
            f"  Shard ({data.get('shard_skill_name', '')}): {data.get('shard_desc', '')}"
        )
    return "\n".join(parts)


def _get_patch_notes(key: str, path: str = None) -> str:
    """Return formatted recent patch notes for a hero or item."""
    if path:
        # notes = patch_data.get("heroes", {}).get(key, {}).get(path)
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


def _add_item_context(header, items):
    item_context = ""
    if items:
        item_context += f"{header}:\n"
        for item in items:
            try:
                item_context += f"{item}: {json.dumps(item_data[item])}\n"
                patch_info = _get_patch_notes(item)
                if patch_info:
                    item_context += f"{item} Recent Patch Notes:\n{patch_info}\n"
            except KeyError:
                continue
    return item_context


# TODO: why tf are the game count numbers so low
def _get_popular_items(hero_id):
    url = f"https://api.opendota.com/api/heroes/{hero_id}/itemPopularity"
    response = get(url).json()
    items = []
    for game_stage in response.keys():
        item_freq = {}
        item_freq["_".join(game_stage.split("_")[:-1])] = list(
            reversed(
                sorted(
                    (
                        (item_data[item]["displayName"], games)
                        for item, games in response[game_stage].items()
                        if item in item_data.keys()
                    ),
                    key=lambda x: x[1],
                )[:5]
            )
        )
        items.append(item_freq)
    return items


def build_gameplay_context(
    pick: str = "",
    enemy_pick: str = "",
    extracted: dict = {},
) -> str:
    context = ""

    # User's hero guide + aghs + patch notes
    if pick:
        # context += _get_hero_guide(pick) + "\n"
        context += f"Popular items for {pick} (item, num_games):\n{_get_popular_items(hero_displayName_to_id[pick])}\n"
        aghs_info = _get_aghs_data(pick)
        if aghs_info:
            context += f"{pick}'s Aghanim's Scepter/Shard Data:\n{aghs_info}"
        patch_info = f"{_get_patch_notes(hero_displayName_to_id[pick], "hero")}\n{_get_patch_notes(hero_displayName_to_id[pick], "abilities")}"
        if patch_info:
            context += f"{pick}'s Recent Patch Notes:\n{patch_info}\n"

    # Enemy same-position hero guide + patch notes
    if enemy_pick:
        # context += _get_hero_guide(enemy_pick) + "\n"
        context += f"Popular items for {enemy_pick} (item, num_games):\n{_get_popular_items(hero_displayName_to_id[enemy_pick])}\n"
        patch_info = _get_patch_notes(hero_displayName_to_id[enemy_pick], "hero")
        if patch_info:
            context += f"{enemy_pick}'s Recent Patch Notes:\n{patch_info}\n"

    # Other heroes mentioned in query
    extracted_heroes = extracted.get("heroes", [])
    extracted_items = extracted.get("items", [])
    item_suggestions = extracted.get("itemSuggestions", [])

    other_heroes = [h for h in extracted_heroes if h not in (pick, enemy_pick)]
    if other_heroes:
        context += "Other Hero Data:\n"
        for hero in other_heroes:
            hero_id = HERO_NAMES.get(hero)
            if hero_id:
                context += json.dumps(hero_data[hero_id]) + "\n"

    # Items mentioned in query
    context += _add_item_context("Item Data", extracted_items)
    # Patch notes for items the LLM might suggest
    context += _add_item_context(
        "Item patch notes for potential suggestions", item_suggestions
    )

    # --- FUTURE: Gemini Synergy/Counter Annotations ---
    # Include pre-computed Gemini annotations about team dynamics
    # relevant to gameplay decisions (target priority, fight plan).
    # --- END FUTURE ---

    return context


def build_draft_context(
    team: dict = {},
    enemy_team: dict = {},
    pos: str = "",
    side: str = "",
) -> str:
    ranked_picks = rank_picks(team, enemy_team, pos)[:NUM_PICKS]

    context = f"\nDRAFT MODE — Top {NUM_PICKS} picks for Position {pos}:\n"
    for hero in ranked_picks:
        name = _hero_display_name(hero.id)
        context += f"  {name}: {hero.score:.1f}\n"

    # TODO: re-add hero guides once regenerated via scripts/generate_hero_guides.py
    # if any(h.id for h in team.values()):
    #     context += "\nAllied hero guides:\n"
    #     for hero in team.values():
    #         if hero.id:
    #             context += _get_hero_guide(_hero_display_name(hero.id)) + "\n"
    # if any(h.id for h in enemy_team.values()):
    #     context += "\nEnemy hero guides:\n"
    #     for hero in enemy_team.values():
    #         if hero.id:
    #             context += _get_hero_guide(_hero_display_name(hero.id)) + "\n"

    # --- FUTURE: Gemini Synergy/Counter Annotations ---
    # Insert pre-computed Gemini annotations for each picked hero here.
    # Format: "Synergy: [hero] + [hero]: [text]" and "Counter: [hero] vs [hero]: [text]"
    # These would come from the frontend state, passed through the API.
    # --- END FUTURE ---

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

    team_label = side.capitalize() if side else "Team"
    enemy_label = (
        "Dire" if side == "radiant" else "Radiant" if side == "dire" else "Enemy"
    )

    if team:
        names = [
            f"{_hero_display_name(h.id)} (Pos {h.pos})" for h in team.values() if h.id
        ]
        if names:
            context += f"{team_label}: {', '.join(names)}\n"
    if enemy_team:
        names = [
            f"{_hero_display_name(h.id)} (Pos {h.pos})"
            for h in enemy_team.values()
            if h.id
        ]
        if names:
            context += f"{enemy_label}: {', '.join(names)}\n"

    context += "Notes:\n"
    context += (
        "- Ability attribute values of 0 may indicate a dynamic or computed value.\n"
    )
    context += "- Ability attributes with 'scepter'/'shard' in the name and empty values indicate the upgrade exists but values are unavailable.\n"
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

    # --- FUTURE: Gemini Synergy/Counter Annotations ---
    # If Gemini annotations have been pre-computed for the current draft state,
    # inject them here as top-level context so both draft and gameplay modes
    # can reference team dynamics (synergy sentences, counter sentences, team game plan).
    # Format: "TEAM DYNAMICS:\n  Synergy: ...\n  Counter: ...\n  Game Plan: ..."
    # --- END FUTURE ---

    if is_draft:
        context += build_draft_context(team, enemy_team, pos, side)
        system = SYSTEM_DRAFT
    elif pick:
        extracted = extract_from(query, pick, enemy_pick)
        context += build_gameplay_context(pick, enemy_pick, extracted)
        system = SYSTEM_GAMEPLAY

    context += build_team_context(team, enemy_team, side, pick, pos)
    prompt = f"{context}\nResponse:"
    print(prompt)
    # resp = _get_response(prompt, model="claude-sonnet-4-6", system=system)
    resp = _get_response(prompt, system=system)
    print(f"\n\n{resp}")
    return resp
