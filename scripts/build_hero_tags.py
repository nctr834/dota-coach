"""
Generate hero capability tags for structured draft/gameplay reasoning.

Calls Claude Haiku for each hero to produce a compact 0-2 rating vector
covering mobility, CC, damage profile, utility, and scaling.

Usage:
    python scripts/build_hero_tags.py [--heroes "Anti-Mage,Axe"] [--model claude-haiku-4-5-20251001]
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

with open("data/hero_data.json") as f:
    hero_data = json.load(f)

TAG_KEYS = [
    "mobility", "initiation", "escape", "waveclear", "tower_damage", "roshan_damage",
    "stun", "root", "silence", "slow", "displacement",
    "burst_damage", "sustained_damage", "magic_damage", "physical_damage", "pure_damage",
    "save", "buff", "heal", "vision", "aura",
    "farm_speed", "item_dependency", "late_game_scaling", "early_game_strength",
]

SYSTEM_PROMPT = """You are a Dota 2 analyst. Rate hero capabilities on a 0-2 scale.
0 = none/negligible, 1 = some/situational, 2 = strong/defining.
Return ONLY a JSON object with the exact keys provided. No commentary."""


def build_hero_prompt(hero: dict) -> str:
    name = hero["displayName"]
    stats = hero.get("stats", {})
    roles = ", ".join(r["roleId"] for r in hero.get("roles", []))

    abilities_text = ""
    for ab in hero.get("abilities", []):
        desc = ab.get("description", "")
        abilities_text += f"  - {ab['displayName']}: {desc}\n"

    facets_text = ""
    for f in hero.get("facets", []):
        facets_text += f"  - {f['name']}: {f.get('description', '')}\n"

    return f"""Rate {name}'s capabilities. Return JSON with these exact keys, each valued 0, 1, or 2:
{json.dumps(TAG_KEYS)}

Scale: 0 = none/negligible, 1 = some/situational, 2 = strong/defining

Hero: {name}
Attack: {stats.get('attackType', '?')} | Primary: {stats.get('primaryAttributeEnum', '?')} | Move Speed: {stats.get('moveSpeed', '?')}
Roles: {roles}

Abilities:
{abilities_text}
Facets:
{facets_text}
Tag definitions:
- mobility: movement abilities (blink, dash, high MS)
- initiation: ability to start fights on enemy team
- escape: ability to disengage from fights
- waveclear: ability to clear creep waves quickly
- tower_damage: ability to damage towers (summons, high attack speed, minus armor)
- roshan_damage: ability to take Roshan efficiently
- stun: hard stun (not root/silence)
- root: root ability
- silence: silence ability
- slow: slow ability
- displacement: forced movement (knockback, pull, hook, swap)
- burst_damage: high damage in short window
- sustained_damage: consistent damage over long fight
- magic_damage: magical damage output
- physical_damage: physical damage output
- pure_damage: pure damage output
- save: ability to save allies (force, grip, grave)
- buff: ability to buff allies (damage, armor, etc.)
- heal: healing allies
- vision: providing vision (wards, flying vision, global abilities)
- aura: aura-based abilities
- farm_speed: how fast the hero farms jungle/waves
- item_dependency: how much the hero needs items to function (support=0, carry=2)
- late_game_scaling: how strong the hero is in late game
- early_game_strength: how strong the hero is in early game (lane dominance, early kills)"""


def validate_tags(data: dict) -> bool:
    """Check all keys present and values are 0, 1, or 2."""
    for key in TAG_KEYS:
        val = data.get(key)
        if val not in (0, 1, 2):
            return False
    return True


def generate_tags(hero: dict, model: str) -> dict | None:
    prompt = build_hero_prompt(hero)
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=256,
                system=[{"type": "text", "text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": prompt}],
            )
            block = response.content[0]
            text = block.text.strip() if block.type == "text" else ""
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
            tags = json.loads(text)
            # Coerce string values to int
            for key in TAG_KEYS:
                if key in tags:
                    tags[key] = int(tags[key])
            if validate_tags(tags):
                return tags
            print(f"  validation failed (attempt {attempt + 1}), retrying...")
        except (json.JSONDecodeError, ValueError, anthropic.InternalServerError, anthropic.RateLimitError) as e:
            print(f"  attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


def main():
    parser = argparse.ArgumentParser(description="Generate hero capability tags")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Claude model")
    parser.add_argument("--heroes", default="", help="Comma-separated hero names (default: all)")
    parser.add_argument("--output", default="data/hero_tags.json", help="Output file")
    args = parser.parse_args()

    output_path = Path(args.output)
    if output_path.exists():
        with open(output_path) as f:
            existing = json.load(f)
    else:
        existing = {}

    if args.heroes:
        target_names = [n.strip() for n in args.heroes.split(",")]
        targets = []
        for name in target_names:
            for hid, h in hero_data.items():
                if h["displayName"] == name:
                    targets.append((hid, h))
                    break
            else:
                print(f"  Skipping {name}: not found")
    else:
        targets = [(hid, h) for hid, h in hero_data.items() if hid not in existing]

    total = len(targets)
    if total == 0:
        print("No heroes to process. Use --heroes to regenerate specific heroes, or delete output file to regenerate all.")
        return

    print(f"Generating tags for {total} heroes using {args.model}...")

    for i, (hero_id, hero) in enumerate(targets):
        name = hero["displayName"]
        print(f"  [{i + 1}/{total}] {name}...", end=" ", flush=True)
        tags = generate_tags(hero, args.model)
        if tags:
            existing[hero_id] = {
                "displayName": name,
                "tags": tags,
            }
            print("done")
        else:
            print("FAILED")
        time.sleep(0.3)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"\nSaved {len(existing)} hero tags to {output_path}")


if __name__ == "__main__":
    main()
