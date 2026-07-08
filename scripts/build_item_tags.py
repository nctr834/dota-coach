"""
Generate item capability tags for grounded itemization talk, per patch.

Same pattern as build_hero_tags.py: Claude Haiku rates each notable item
(rare/epic/artifact, cost >= 1000) on a 0-2 vector from Valve's own item text
in data/item_data.json. Output is reviewable data; the runtime agent only
reads it (tools surface the 2s as descriptive tags).

Usage:
    python scripts/build_item_tags.py [--items "Eye of Skadi,Nullifier"] [--model ...]
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

from utils import _notable
from data_loader import item_data

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

TAG_KEYS = [
    "mobility", "initiation", "escape",
    "disable", "silence", "root", "slow",
    "burst_damage", "sustained_damage", "aoe_damage", "cleave",
    "attack_speed", "crit", "lifesteal",
    "survivability", "armor", "magic_resist", "spell_immunity", "evasion",
    "self_dispel", "enemy_dispel", "break",
    "anti_heal", "anti_illusion", "true_sight", "invisibility", "illusions",
    "save_ally", "aura", "tower_damage", "farm_acceleration", "mana_sustain",
]

SYSTEM_PROMPT = """You are a Dota 2 analyst. Rate item capabilities on a 0-2 scale.
0 = none/negligible, 1 = some/situational, 2 = strong/defining.
Return ONLY a JSON object with the exact keys provided. No commentary."""


def build_item_prompt(short: str, item: dict) -> str:
    name = item["displayName"]
    return f"""Rate {name}'s capabilities. Return JSON with these exact keys, each valued 0, 1, or 2:
{json.dumps(TAG_KEYS)}

Scale: 0 = none/negligible, 1 = some/situational, 2 = strong/defining

Item: {name} (cost {item.get('cost', '?')})
Description: {item.get('description', '')}
Values: {item.get('values', '')}
Notes: {item.get('notes', '')}
Shop tags: {item.get('shopTags', '')}

Tag definitions:
- mobility: movement actives (blink, force, greaves speed)
- initiation: starts fights (blink-in, hex, chain opener)
- escape: disengage tool
- disable: hard disable on an enemy (stun, hex, banish)
- silence / root / slow: applies that effect to enemies
- burst_damage: high damage in a short window
- sustained_damage: raises consistent DPS
- aoe_damage / cleave: multi-target damage
- attack_speed / crit / lifesteal: grants that stat meaningfully
- survivability: raw tankiness (HP, all-stats bulk, damage barriers)
- armor / magic_resist: grants that defense meaningfully
- spell_immunity: grants spell immunity
- evasion: grants evasion
- self_dispel: removes debuffs from self/ally on use
- enemy_dispel: dispels buffs off enemies (Nullifier-style)
- break: disables enemy passives
- anti_heal: reduces enemy healing
- anti_illusion: strong vs illusions (AoE that clears them counts as 1)
- true_sight / invisibility / illusions: grants that
- save_ally: can save another hero (targetable defensive active)
- aura: teamwide/area passive effect
- tower_damage: helps take buildings (demolish, minus armor, summons)
- farm_acceleration: speeds up farming (cleave, AoE clear, Midas)
- mana_sustain: solves mana problems"""


def validate_tags(data: dict) -> bool:
    return all(data.get(k) in (0, 1, 2) for k in TAG_KEYS)


def generate_tags(short: str, item: dict, model: str) -> dict | None:
    prompt = build_item_prompt(short, item)
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=500,
                system=[{"type": "text", "text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                b.text for b in response.content if b.type == "text"
            ).strip()
            if response.stop_reason != "end_turn":
                print(f"  stop_reason={response.stop_reason}", end=" ")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
            tags = json.loads(text)
            for key in TAG_KEYS:
                if key in tags:
                    tags[key] = int(tags[key])
            if validate_tags(tags):
                return tags
            print(f"  validation failed (attempt {attempt + 1}), retrying...")
        except (
            json.JSONDecodeError,
            ValueError,
            anthropic.InternalServerError,
            anthropic.RateLimitError,
        ) as e:
            print(f"  attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(2**attempt)
    return None


def main():
    parser = argparse.ArgumentParser(description="Generate item capability tags")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--items", default="", help="Comma-separated display names (default: all notable)")
    parser.add_argument("--output", default="data/item_tags.json")
    args = parser.parse_args()

    output_path = Path(args.output)
    existing = json.loads(output_path.read_text()) if output_path.exists() else {}

    notable = {
        k.removeprefix("item_"): v
        for k, v in item_data.items()
        if _notable(k.removeprefix("item_"))
    }
    if args.items:
        wanted = {n.strip() for n in args.items.split(",")}
        targets = [(s, v) for s, v in notable.items() if v["displayName"] in wanted]
    else:
        targets = [(s, v) for s, v in notable.items() if s not in existing]

    if not targets:
        print("No items to process. Use --items to regenerate, or delete the output file.")
        return
    print(f"Generating tags for {len(targets)} items using {args.model}...")

    for i, (short, item) in enumerate(targets):
        print(f"  [{i + 1}/{len(targets)}] {item['displayName']}...", end=" ", flush=True)
        tags = generate_tags(short, item, args.model)
        if tags:
            existing[short] = {"displayName": item["displayName"], "tags": tags}
            print("done")
        else:
            print("FAILED")
        time.sleep(0.3)

    output_path.write_text(json.dumps(existing, indent=2))
    print(f"\nSaved {len(existing)} item tags to {output_path}")


if __name__ == "__main__":
    main()
