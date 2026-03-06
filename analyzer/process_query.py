import chromadb
import json
import anthropic
import os
import time
from rapidfuzz import process, fuzz
from json_repair import repair_json
from dotenv import load_dotenv
from evaluator import rank_picks

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)
with open("data/item_data.json", "r") as f:
    item_data = json.load(f)
with open("data/patch_notes.json", "r") as f:
    patch_notes = json.load(f)
with open("data/RAG/RAG_content_heroes.json", "r") as f:
    hero_guides = json.load(f)
with open("data/aghs_data.json", "r") as f:
    aghs_data = json.load(f)

chromadb_client = chromadb.PersistentClient(path="data/vectordb")
insights_collection = chromadb_client.get_collection("dota_insights")
guides_collection = chromadb_client.get_collection("dota_guides")

HERO_NAMES = {hero["displayName"]: hero["id"] for hero in hero_data.values()}
ITEM_NAMES = {item["displayName"] for item in item_data.values()}
IS_DRAFT = False
NUM_PICKS = 10

SYSTEM_GAMEPLAY = [
    {
        "type": "text",
        "text": """You are a high-MMR Dota 2 coach. Give direct, practical advice, but BE CONCISE.
- Be concise. No filler.
- Focus on win conditions, timing windows, and specific decision-making.
- Be opinionated about item choices and stress item timings and how to capitalize on them.
- Don't fixate on a single matchup — consider the broader team dynamics and what each hero wants to achieve. Sometimes it's better to target supports than the carry.
- Position 1 is carry, 2 is mid, 3 is offlane, 4 is soft support, 5 is hard support. Pos 1+5 lane vs pos 3+4.
- When asked about targeting or fight plans, consider all enemies and their roles, not just the direct matchup.
- Do NOT hallucinate item interactions or ability mechanics — rely on the provided data.
- If unsure about a specific interaction, say so.""",
    }
]

SYSTEM_DRAFT = [
    {
        "type": "text",
        "text": """You are a high-MMR Dota 2 draft coach. Help the user pick the best hero for their draft.
- You are given ranked candidate picks with scores. Explain WHY the top picks are strong in this context.
- Consider team synergy, enemy counters, lane matchups, and win conditions.
- Position 1 is carry, 2 is mid, 3 is offlane, 4 is soft support, 5 is hard support. Pos 1+5 lane vs pos 3+4.
- Be opinionated — recommend 2-3 picks and explain tradeoffs between them.
- Consider what the team lacks (initiation, save, wave clear, lockdown) and what the enemy is weak to.
- Do NOT hallucinate ability mechanics — rely on the provided data.""",
    }
]


def _get_response(
    prompt: str,
    tokens: int = 2048,
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
    return hero_data.get(str(hero_id), {}).get("displayName", f"Hero#{hero_id}")


def _get_hero_guide(name: str) -> str:
    guide = hero_guides.get(name)
    if not guide:
        return ""
    parts = [f"Hero Guide — {name}"]
    for field in [
        "position",
        "goal",
        "requires",
        "fights",
        "dont",
        "timings",
        "gameStage",
        "patchNotes",
    ]:
        if guide.get(field):
            parts.append(f"  {field}: {guide[field]}")
    return "\n".join(parts)


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


def _get_aghs_data(hero):
    pass


def _get_patch_notes(key):
    pass


def build_gameplay_context(
    query: str,
    radiant: dict = {},
    dire: dict = {},
    pick: str = "",
    pos: str = "",
    side: str = "",
) -> str:
    enemy_pick = (
        _hero_display_name(dire[int(pos)].id)
        if side == "radiant"
        else _hero_display_name(radiant[int(pos)].id)
    )
    extracted = extract_from(query, pick, enemy_pick)
    heroes = extracted.get("heroes", [])
    items = extracted.get("items", [])
    item_suggestions = extracted.get("itemSuggestions", [])

    context = ""

    # Direct hero guide lookup for user pick + enemy same-role pick
    if pick:
        context += _get_hero_guide(pick) + "\n"
        # context += f"{pick}'s Aghanim's Scepter/Shard Data:\n"
        # context += _get_aghs_data(pick) + "\n"
        # context += f"{pick}'s Patch Notes from recent patches:\n"
        # context += _get_patch_notes(pick) + "\n"
    if enemy_pick:
        context += _get_hero_guide(enemy_pick) + "\n"
        # context += f"{enemy_pick}'s Patch Notes from recent patches:\n"
        # context += _get_patch_notes(enemy_pick) + "\n"

    # Raw hero data for any other heroes mentioned in query
    other_heroes = [h for h in heroes if h not in (pick, enemy_pick)]
    if other_heroes:
        context += "Other Hero Data:\n"
        for hero in other_heroes:
            hero_id = HERO_NAMES.get(hero)
            if hero_id:
                context += json.dumps(hero_data[str(hero_id)]) + "\n"

    if items:
        context += "Item Data:\n"
        for item in items:
            context += f"{item}: {json.dumps(item_data[item])}\n"
            context += f"{item} Recent Patch Notes:\n"
            context += _get_patch_notes(item) + "\n"
    if item_suggestions:
        context += (
            "Item change patch notes based on what you may suggest (IMPORTANT!):\n"
        )
        for item in item_suggestions:
            if item in patch_notes["items"]:
                patches = patch_notes["items"][item].values()
                for patch in patches:
                    if patch:
                        context += f"{item}: {', '.join(p for p in patch)}\n"

    # retrieval_query = f"{pick} {query} {' '.join(heroes + items)}"
    insights = insights_collection.query(query_texts=[retrieval_query], n_results=3)
    item_guides = guides_collection.query(query_texts=[retrieval_query], n_results=5)
    # context += "INSIGHTS:\n"
    # context += "\n".join(insights["documents"][0]) + "\n"
    # context += "ITEM GUIDES:\n"
    # context += "\n".join(item_guides["documents"][0])
    return context


def build_draft_context(
    radiant: dict = {},
    dire: dict = {},
    pick: str = "",
    pos: str = "",
    side: str = "",
) -> str:
    team = radiant if side == "radiant" else dire
    enemy_team = dire if side == "radiant" else radiant
    ranked_picks = rank_picks(team, enemy_team, pos)[:NUM_PICKS]

    context = f"\nDRAFT MODE — Top {NUM_PICKS} picks for Position {pos}:\n"
    for hero in ranked_picks:
        name = _hero_display_name(hero.id)
        context += f"  {name}: {hero.score:.1f}\n"

    if any(h.id for h in team.values()):
        context += "\nAllied hero guides:\n"
        for hero in team.values():
            if hero.id:
                name = _hero_display_name(hero.id)
                context += _get_hero_guide(name) + "\n"

    if any(h.id for h in enemy_team.values()):
        context += "\nEnemy hero guides:\n"
        for hero in enemy_team.values():
            if hero.id:
                name = _hero_display_name(hero.id)
                context += _get_hero_guide(name) + "\n"

    return context


def build_context_body(
    radiant: dict = {}, dire: dict = {}, pick: str = "", pos: str = "", side: str = ""
) -> str:
    context = "\nADDITIONAL CONTEXT:\n"
    if pick:
        context += f"User picked: {pick}\n"
    if pos:
        context += f"User role: Position {pos}\n"
    if side:
        context += f"User side: {side}\n"
    if radiant or dire:
        if radiant:
            names = [
                f"{_hero_display_name(h.id)} (Pos {h.pos})"
                for h in radiant.values()
                if h.id
            ]
            context += f"Radiant: {', '.join(names)}\n"
        if dire:
            names = [
                f"{_hero_display_name(h.id)} (Pos {h.pos})"
                for h in dire.values()
                if h.id
            ]
            context += f"Dire: {', '.join(names)}\n"
    context += "Notes:\n"
    context += (
        "- Ability attribute values of 0 may indicate a dynamic or computed value.\n"
    )
    context += "- Ability attributes with 'scepter'/'shard' in the name and empty values indicate the upgrade exists but values are unavailable.\n"
    return context


def generate_response(
    query: str,
    radiant: dict = {},
    dire: dict = {},
    pick: str = "",
    pos: str = "",
    side: str = "",
) -> str:
    context = f"QUERY: {query}\n"
    if IS_DRAFT:
        context += build_draft_context(radiant, dire, pick, pos, side)
        system = SYSTEM_DRAFT
    else:
        context += build_gameplay_context(query, radiant, dire, pick, pos, side)
        system = SYSTEM_GAMEPLAY
    context += build_context_body(radiant, dire, pick, pos, side)
    prompt = f"{context}\nResponse:"
    print(prompt)
    resp = _get_response(prompt, system=system)
    print(f"\n\n{resp}")
    return resp
