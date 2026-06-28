"""
Regenerate RAG_content_heroes.json from actual game data.

Uses hero_data.json, patch_data.json, pos_data.json to build a prompt
per hero, then asks Claude to generate a concise strategy guide.

Usage:
    python scripts/generate_hero_guides.py [--model claude-haiku-4-5-20251001] [--heroes "Anti-Mage,Axe"]
"""

import json
import os
import sys
import time
import argparse
from pathlib import Path
from dotenv import load_dotenv

import anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "analyzer"))
os.chdir(PROJECT_ROOT)

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Load data
with open("data/hero_data.json") as f:
    hero_data = json.load(f)
with open("data/patch_data.json") as f:
    patch_data = json.load(f)
with open("data/pos_data.json") as f:
    pos_data = json.load(f)

hero_name_to_id = {h["displayName"]: hid for hid, h in hero_data.items()}

POS_NAMES = {
    "1": "Carry (Pos 1)",
    "2": "Mid (Pos 2)",
    "3": "Offlane (Pos 3)",
    "4": "Soft Support (Pos 4)",
    "5": "Hard Support (Pos 5)",
}


def get_hero_positions(hero_id: str) -> list[dict]:
    """Get viable positions for a hero based on pick rate."""
    positions = []
    for pos_key, heroes in pos_data.items():
        entry = heroes.get(str(hero_id))
        if entry and entry["matchCount"] >= 50:
            wr = (
                entry["winCount"] / entry["matchCount"]
                if entry["matchCount"] > 0
                else 0
            )
            positions.append(
                {
                    "position": pos_key,
                    "label": POS_NAMES.get(pos_key, f"Pos {pos_key}"),
                    "winRate": round(wr * 100, 1),
                    "matches": entry["matchCount"],
                }
            )
    positions.sort(key=lambda x: x["matches"], reverse=True)
    return positions


def get_popular_items(hero_id: str) -> list[dict]:
    """Get item info from hero abilities and facets (no longer calls OpenDota)."""
    # With the new data structure, we don't have item popularity data.
    # Return empty — the guide prompt handles this gracefully.
    return []


def get_aghs_info(hero_id: str) -> str:
    hero = hero_data.get(str(hero_id), {})
    parts = []
    for ability in hero.get("abilities", []):
        if ability.get("scepter_description"):
            parts.append(f"Scepter ({ability['displayName']}): {ability['scepter_description']}")
        if ability.get("shard_description"):
            parts.append(f"Shard ({ability['displayName']}): {ability['shard_description']}")
    return "\n".join(parts) if parts else "None"


def get_patch_notes(hero_id: str) -> str:
    hero_patches = patch_data.get("heroes", {}).get(str(hero_id), {})
    parts = []
    for section in ["hero", "abilities"]:
        section_data = hero_patches.get(section, {})
        if isinstance(section_data, dict):
            for version, notes in section_data.items():
                if notes:
                    parts.append(f"{version}: {notes}")
        elif isinstance(section_data, str) and section_data:
            parts.append(section_data)
    return "\n".join(parts[-6:]) if parts else "No recent changes"


def get_abilities_summary(hero: dict) -> str:
    parts = []
    for ability in hero.get("abilities", []):
        name = ability.get("displayName", ability.get("name", "Unknown"))
        parts.append(name)
    return ", ".join(parts)


def generate_guide(hero: dict, model: str) -> dict | None:
    hero_id = hero["id"]
    name = hero["displayName"]
    positions = get_hero_positions(hero_id)
    pos_str = "; ".join(
        f"{p['label']} ({p['winRate']}% WR, {p['matches']} games)" for p in positions
    )
    if not pos_str:
        pos_str = "No significant position data"

    aghs = get_aghs_info(hero_id)
    patches = get_patch_notes(hero_id)
    abilities = get_abilities_summary(hero)
    roles = ", ".join(r["roleId"] for r in hero.get("roles", []))
    stats = hero.get("stats", {})

    # Get popular items (with rate limiting)
    items = get_popular_items(hero_id)
    items_str = ""
    for stage in items:
        items_str += f"  {stage['stage']}: {', '.join(f'{n} ({g})' for n, g in stage['items'])}\n"
    if not items_str:
        items_str = "  (unavailable)\n"

    prompt = f"""Generate a concise Dota 2 strategy guide for {name} on patch 7.40. Use ONLY the data below.

HERO: {name}
Roles: {roles}
Attack: {stats.get('attackType', 'Unknown')} | Primary: {stats.get('primaryAttributeEnum', 'Unknown')} | Move Speed: {stats.get('moveSpeed', '?')} | Range: {stats.get('attackRange', '?')}
Abilities: {abilities}
Aghanim's: {aghs}
Positions: {pos_str}
Popular Items:
{items_str}
Recent Patch Notes:
{patches}

Return ONLY a JSON object with these fields (each 1-2 sentences max):
- "goal": What this hero wants to achieve in a game
- "requires": Key prerequisites/conditions to win with this hero
- "fights": How to approach teamfights
- "dont": Critical mistakes to avoid
- "timings": Key item/level timing windows
- "gameStage": Brief early/mid/late game plan"""

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            block = response.content[0]
            text = block.text.strip() if block.type == "text" else ""
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
            guide = json.loads(text)
            guide["id"] = hero_id
            guide["position"] = (
                pos_str.split(";")[0].strip() if positions else "Unknown"
            )
            guide["positionNumber"] = (
                [int(p["position"]) for p in positions[:2]] if positions else []
            )
            return guide
        except (
            json.JSONDecodeError,
            anthropic.InternalServerError,
            anthropic.RateLimitError,
        ) as e:
            print(f"  Attempt {attempt + 1} failed for {name}: {e}")
            if attempt < 2:
                time.sleep(2**attempt)
    return None


def main():
    parser = argparse.ArgumentParser(description="Generate hero strategy guides")
    parser.add_argument(
        "--model", default="claude-haiku-4-5-20251001", help="Claude model to use"
    )
    parser.add_argument(
        "--heroes",
        default="",
        help="Comma-separated hero names to generate (default: all)",
    )
    parser.add_argument(
        "--output", default="data/RAG/RAG_content_heroes.json", help="Output file path"
    )
    args = parser.parse_args()

    # Load existing guides if any (for incremental updates)
    output_path = Path(args.output)
    if output_path.exists():
        with open(output_path) as f:
            existing = json.load(f)
    else:
        existing = {}

    # Determine which heroes to generate
    if args.heroes:
        target_names = [n.strip() for n in args.heroes.split(",")]
    else:
        target_names = [h["displayName"] for h in hero_data.values()]

    total = len(target_names)
    print(f"Generating guides for {total} heroes using {args.model}...")

    for i, name in enumerate(target_names):
        hero_id = hero_name_to_id.get(name)
        if not hero_id:
            print(
                f"  [{i+1}/{total}] Skipping {name}: not found in hero_displayName_to_id.json"
            )
            continue

        hero = hero_data.get(str(hero_id))
        if not hero:
            print(f"  [{i+1}/{total}] Skipping {name}: not found in hero_data.json")
            continue

        print(f"  [{i+1}/{total}] {name}...", end=" ", flush=True)
        guide = generate_guide(hero, args.model)
        if guide:
            existing[name] = guide
            print("done")
        else:
            print("FAILED")

        # Rate limiting
        time.sleep(0.5)

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"\nSaved {len(existing)} guides to {output_path}")


if __name__ == "__main__":
    main()
