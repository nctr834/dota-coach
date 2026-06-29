# TODO: refactor. ~850 lines across 6 gather phases with hand-rolled VPKR
# brace-tracking parsers (see the facet loop). Could split per-source and use
# the already-imported `vdf` library instead of manual string matching.
import json
import os
import re
from enum import Enum
from pathlib import Path
from dotenv import load_dotenv
import requests
from requests import get
import vdf

os.chdir(Path(__file__).resolve().parent.parent)

load_dotenv()
token = os.getenv("STRATZ_API_KEY")


def _stratz(query: str) -> dict:
    resp = requests.post(
        "https://api.stratz.com/graphql",
        json={"query": query},
        headers={"Authorization": f"Bearer {token}", "User-Agent": "STRATZ_API"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data") or {}


Path("data").mkdir(parents=True, exist_ok=True)
Path("frontend/src/data").mkdir(parents=True, exist_ok=True)

D2VPKR_BASE = "https://raw.githubusercontent.com/dotabuff/d2vpkr/refs/heads/master"

ignore = {
    "hero_data": False,
    "matchup_data": False,
    "pos_data": False,
    "item_data": False,
    "patch_data": False,
    "aghs_data": False,
}

# Valve's internal item names → actual in-game display names.
# The d2vpkr localization files don't have tooltip keys for many items,
# so the fallback title-cases the internal name (e.g. "Bfury", "Cyclone").
ITEM_NAME_OVERRIDES = {
    "item_blink": "Blink Dagger",
    "item_bfury": "Battle Fury",
    "item_cyclone": "Eul's Scepter of Divinity",
    "item_heart": "Heart of Tarrasque",
    "item_assault": "Assault Cuirass",
    "item_ghost": "Ghost Scepter",
    "item_sphere": "Linken's Sphere",
    "item_pipe": "Pipe of Insight",
    "item_manta": "Manta Style",
    "item_orchid": "Orchid Malevolence",
    "item_sheepstick": "Scythe of Vyse",
    "item_invis_sword": "Shadow Blade",
    "item_greater_crit": "Daedalus",
    "item_lesser_crit": "Crystalys",
    "item_rapier": "Divine Rapier",
    "item_pers": "Perseverance",
    "item_flask": "Healing Salve",
    "item_tpscroll": "Town Portal Scroll",
    "item_boots": "Boots of Speed",
    "item_branches": "Iron Branch",
    "item_eagle": "Eaglesong",
    "item_gem": "Gem of True Sight",
    "item_dust": "Dust of Appearance",
    "item_lifesteal": "Morbid Mask",
    "item_vladmir": "Vladmir's Offering",
    "item_armlet": "Armlet of Mordiggian",
    "item_ancient_janggo": "Drum of Endurance",
    "item_refresher": "Refresher Orb",
    "item_basher": "Skull Basher",
    "item_skadi": "Eye of Skadi",
    "item_gauntlets": "Gauntlets of Strength",
    "item_mantle": "Mantle of Intelligence",
    "item_robe": "Robe of the Magi",
    "item_slippers": "Slippers of Agility",
    "item_sobi_mask": "Sage's Mask",
    "item_gloves": "Gloves of Haste",
    "item_boots_of_elves": "Band of Elvenskin",
    "item_relic": "Sacred Relic",
    "item_ward_observer": "Observer Ward",
    "item_ward_sentry": "Sentry Ward",
    "item_gungir": "Gleipnir",
    "item_travel_boots": "Boots of Travel",
    "item_travel_boots_2": "Boots of Travel 2",
    "item_ultimate_scepter": "Aghanim's Scepter",
    "item_ultimate_scepter_2": "Aghanim's Blessing",
    "item_aghanims_shard": "Aghanim's Shard",
    "item_heavens_halberd": "Heaven's Halberd",
    "item_shivas_guard": "Shiva's Guard",
    "item_revenants_brooch": "Revenant's Brooch",
    "item_blight_stone": "Orb of Blight",
    "item_caster_rapier": "Parasma",
    "item_devastator": "Harpoon",
    "item_angels_demise": "Khanda",
    "item_grandmasters_glaive": "Grandmaster's Glaive",
    "item_witches_switch": "Witch's Switch",
}


class Rank(Enum):
    HERALD_GUARDIAN = "HERALD_GUARDIAN"
    CRUSADER_ARCHON = "CRUSADER_ARCHON"
    LEGEND_ANCIENT = "LEGEND_ANCIENT"
    DIVINE_IMMORTAL = "DIVINE_IMMORTAL"


def _fetch_vdf(url: str) -> dict:
    """Fetch a VDF file from GitHub and parse it."""
    text = get(url).text
    # Fix malformed entries: a key on one line followed by a bare "" on the next
    # e.g. "SomeKey"\n"" → "SomeKey"\t\t""
    text = re.sub(r'("[\w]+")\s*\n(\s*"")\s*\n', r'\1\t\t""\n', text)
    return vdf.loads(text)


def _fetch_localization(url: str) -> dict:
    """Fetch a Valve localization VDF file and return the flat Tokens dict."""
    data = _fetch_vdf(url)
    return data.get("lang", {}).get("Tokens", {})


def _parse_hero_list_from_npc_heroes(text: str) -> dict:
    """Parse npc_heroes.txt (which vdf.loads chokes on) with regex.

    Returns {shortName: {"id": int, "roles": [...], "stats": {...}, "abilities": [...], "talents": [...], "facets": [...]}}.
    """
    lines = text.split("\n")
    heroes = {}
    current_hero = None
    current_block = []
    brace_depth = 0

    for line in lines:
        stripped = line.strip().strip('"')
        # Detect hero header: standalone "npc_dota_hero_xyz" line
        if (
            stripped.startswith("npc_dota_hero_")
            and stripped != "npc_dota_hero_base"
            and "\t" not in stripped
            and current_hero is None
        ):
            current_hero = stripped.replace("npc_dota_hero_", "")
            current_block = [line]
            brace_depth = 0
            continue

        if current_hero is not None:
            current_block.append(line)
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0 and len(current_block) > 2:
                heroes[current_hero] = _extract_hero_info(current_block)
                current_hero = None
                current_block = []

    return heroes


def _val(lines: list, key: str, default: str = "") -> str:
    """Extract a simple key-value from VDF lines."""
    for line in lines:
        m = re.search(rf'"{key}"\s+"([^"]*)"', line)
        if m:
            return m.group(1)
    return default


def _extract_hero_info(block_lines: list) -> dict:
    """Extract relevant info from a hero block in npc_heroes.txt."""
    hero_id = int(_val(block_lines, "HeroID", "0"))
    display_name = _val(block_lines, "workshop_guide_name", "")
    roles_str = _val(block_lines, "Role", "")
    roles = [{"roleId": r.strip().upper()} for r in roles_str.split(",") if r.strip()]

    attack_cap = _val(block_lines, "AttackCapabilities", "")
    if "MELEE" in attack_cap:
        attack_type = "Melee"
    elif "RANGED" in attack_cap:
        attack_type = "Ranged"
    else:
        attack_type = "Unknown"

    attr_primary = _val(block_lines, "AttributePrimary", "")
    primary_map = {
        "DOTA_ATTRIBUTE_STRENGTH": "STR",
        "DOTA_ATTRIBUTE_AGILITY": "AGI",
        "DOTA_ATTRIBUTE_INTELLECT": "INT",
        "DOTA_ATTRIBUTE_ALL": "UNI",
    }
    primary_attr = primary_map.get(attr_primary, "UNI")

    stats = {
        "attackType": attack_type,
        "primaryAttributeEnum": primary_attr,
        "startingArmor": float(_val(block_lines, "ArmorPhysical", "0")),
        "moveSpeed": int(_val(block_lines, "MovementSpeed", "0")),
        "attackRange": int(_val(block_lines, "AttackRange", "0")),
        "attackRate": float(_val(block_lines, "AttackRate", "0")),
        "baseStr": int(_val(block_lines, "AttributeBaseStrength", "0")),
        "baseAgi": int(_val(block_lines, "AttributeBaseAgility", "0")),
        "baseInt": int(_val(block_lines, "AttributeBaseIntelligence", "0")),
        "strGain": float(_val(block_lines, "AttributeStrengthGain", "0")),
        "agiGain": float(_val(block_lines, "AttributeAgilityGain", "0")),
        "intGain": float(_val(block_lines, "AttributeIntelligenceGain", "0")),
    }

    # Abilities (Ability1-Ability9)
    abilities = []
    for i in range(1, 10):
        ab = _val(block_lines, f"Ability{i}")
        if ab and not ab.startswith("special_bonus"):
            abilities.append(ab)

    # Talents (Ability10-Ability17, paired: 10/11=lv10, 12/13=lv15, etc.)
    talent_slots = {}
    for i in range(10, 18):
        t = _val(block_lines, f"Ability{i}")
        if t:
            talent_slots[i] = t
    talents = {}
    level_map = {
        10: "10",
        11: "10",
        12: "15",
        13: "15",
        14: "20",
        15: "20",
        16: "25",
        17: "25",
    }
    for slot, name in talent_slots.items():
        lvl = level_map.get(slot, "?")
        talents.setdefault(lvl, []).append(name)

    # Facets. Valve kept the facet definitions in npc_heroes.txt but flagged
    # them "Deprecated" "true" after 7.41 removed the mechanic, so skip those.
    facets = []
    in_facets = False
    facet_depth = 0
    current_facet = None
    for line in block_lines:
        stripped = line.strip()
        if '"Facets"' in stripped and "{" not in stripped:
            in_facets = True
            continue
        if in_facets:
            if "{" in stripped:
                facet_depth += stripped.count("{")
            if "}" in stripped:
                facet_depth -= stripped.count("}")
            if facet_depth <= 0 and in_facets and facet_depth != 0:
                break
            if facet_depth == 0 and "}" in stripped:
                in_facets = False
                continue
            lowered = stripped.lower()
            if '"deprecated"' in lowered and '"true"' in lowered:
                if facets and facets[-1].get("name") == current_facet:
                    facets.pop()
            # A facet name line is a bare quoted string before its opening brace
            clean = stripped.strip('"')
            if (
                facet_depth == 1
                and clean
                and not clean.startswith("{")
                and not clean.startswith("}")
                and "\t" not in stripped.replace("\t\t", "").strip('"')
            ):
                # Check if this is a key-value pair or a standalone key
                parts = stripped.split("\t")
                parts = [p.strip().strip('"') for p in parts if p.strip()]
                if len(parts) == 1:
                    current_facet = parts[0]
                    facets.append({"name": current_facet})

    return {
        "id": hero_id,
        "displayName": display_name,
        "roles": roles,
        "stats": stats,
        "ability_names": abilities,
        "talents": talents,
        "facet_keys": [f["name"] for f in facets],
    }


def gather_hero_game_data():
    """Fetch hero data from d2vpkr: stats from npc_heroes.txt, abilities from
    individual hero files, descriptions from abilities_english.txt, tips from
    tips_english.txt."""
    if ignore["hero_data"]:
        return

    print("Fetching hero list from npc_heroes.txt...")
    heroes_text = get(f"{D2VPKR_BASE}/dota/scripts/npc/npc_heroes.txt").text
    hero_info = _parse_hero_list_from_npc_heroes(heroes_text)
    print(f"  Found {len(hero_info)} heroes in npc_heroes.txt")

    print("Fetching abilities_english.txt...")
    loc_tokens = _fetch_localization(
        f"{D2VPKR_BASE}/dota/resource/localization/abilities_english.txt"
    )

    print("Fetching tips_english.txt...")
    tips_tokens = _fetch_localization(
        f"{D2VPKR_BASE}/dota/resource/localization/tips_english.txt"
    )

    hero_data = {}
    failed = []

    # Filter out non-playable entries
    skip_heroes = {"target_dummy"}

    for short_name, info in hero_info.items():
        if short_name in skip_heroes:
            continue
        hero_id = str(info["id"])
        display_name = info["displayName"] or short_name.replace("_", " ").title()

        # Fetch individual hero ability file
        url = f"{D2VPKR_BASE}/dota/scripts/npc/heroes/npc_dota_hero_{short_name}.txt"
        try:
            ability_vdf = _fetch_vdf(url)
        except Exception as e:
            failed.append((short_name, str(e)))
            ability_vdf = {}

        ability_defs = ability_vdf.get("DOTAAbilities", {})

        # Build abilities list
        abilities = []
        for ab_name in info["ability_names"]:
            ab_data = ability_defs.get(ab_name, {})
            if not ab_data or not isinstance(ab_data, dict):
                continue

            # Get localization
            loc_prefix = f"DOTA_Tooltip_ability_{ab_name}"
            ab_display_name = loc_tokens.get(
                loc_prefix,
                ab_name.replace(short_name + "_", "").replace("_", " ").title(),
            )
            description = loc_tokens.get(f"{loc_prefix}_Description", "")
            lore = loc_tokens.get(f"{loc_prefix}_Lore", "")
            scepter_desc = loc_tokens.get(f"{loc_prefix}_scepter_description", "")
            shard_desc = loc_tokens.get(f"{loc_prefix}_shard_description", "")

            # Collect notes
            notes = []
            for n in range(10):
                note = loc_tokens.get(f"{loc_prefix}_Note{n}")
                if note:
                    notes.append(note)

            # Parse AbilityValues
            raw_values = ab_data.get("AbilityValues", {})
            values = {}
            for vk, vv in raw_values.items():
                if isinstance(vv, dict):
                    val = vv.get("value", "")
                    if val == "0" or val == "0.0":
                        # Check if unlocked by talent/facet/shard/scepter
                        bonus_keys = [k for k in vv if k.startswith("special_bonus")]
                        if bonus_keys:
                            # Determine unlock source
                            sources = []
                            for bk in bonus_keys:
                                if "scepter" in bk:
                                    sources.append("scepter")
                                elif "shard" in bk:
                                    sources.append("shard")
                                elif "facet" in bk:
                                    sources.append("facet")
                                else:
                                    sources.append("talent")
                            val = f"variable ({'/'.join(dict.fromkeys(sources))})"
                        else:
                            val = "variable"
                    values[vk] = val
                else:
                    values[vk] = vv

            # Cooldown/mana from top-level or AbilityValues
            cooldown = ab_data.get("AbilityCooldown", "")
            if not cooldown and isinstance(raw_values.get("AbilityCooldown"), dict):
                cooldown = raw_values["AbilityCooldown"].get("value", "")
            mana_cost = ab_data.get("AbilityManaCost", "")

            has_scepter = ab_data.get("HasScepterUpgrade", "0") == "1"
            has_shard = ab_data.get("HasShardUpgrade", "0") == "1"

            ability_entry = {
                "name": ab_name,
                "displayName": ab_display_name,
                "description": description,
                "cooldown": cooldown,
                "manaCost": mana_cost,
                "values": values,
                "notes": notes,
            }
            if scepter_desc or has_scepter:
                ability_entry["scepter_description"] = scepter_desc
            if shard_desc or has_shard:
                ability_entry["shard_description"] = shard_desc

            abilities.append(ability_entry)

        # Build facets with localization
        facets = []
        for fk in info.get("facet_keys", []):
            facet_loc_prefix = f"DOTA_Tooltip_Facet_{fk}"
            facet_name = loc_tokens.get(facet_loc_prefix, fk.replace("_", " ").title())
            facet_desc = loc_tokens.get(f"{facet_loc_prefix}_Description", "")
            facets.append({"name": facet_name, "description": facet_desc})

        # Build talent descriptions from localization
        talents = {}
        for lvl, talent_names in info["talents"].items():
            talent_descs = []
            for tn in talent_names:
                talent_loc = loc_tokens.get(f"DOTA_Tooltip_ability_{tn}", tn)
                talent_descs.append(talent_loc)
            talents[lvl] = talent_descs

        # Gather tips
        tips = []
        for i in range(1, 20):
            tip = tips_tokens.get(f"dota_tip_hero_{short_name}_{i}")
            if tip:
                tips.append(tip)

        hero_data[hero_id] = {
            "id": hero_id,
            "displayName": display_name,
            "shortName": short_name,
            "stats": info["stats"],
            "abilities": abilities,
            "facets": facets,
            "talents": talents,
            "tips": tips,
            "roles": info["roles"],
        }

    if failed:
        print(f"  Failed to fetch ability files for: {[f[0] for f in failed]}")

    Path("data/hero_data.json").write_text(json.dumps(hero_data))
    Path("frontend/src/data/hero_data.json").write_text(json.dumps(hero_data))
    print(f"Hero data gathered ({len(hero_data)} heroes)")
    return hero_data


def gather_item_game_data():
    """Fetch item data from d2vpkr items.txt + ability descriptions from
    abilities_english.txt."""
    if ignore["item_data"]:
        return

    print("Fetching items.txt...")
    items_vdf = _fetch_vdf(f"{D2VPKR_BASE}/dota/scripts/npc/items.txt")
    all_items = items_vdf.get("DOTAAbilities", {})

    print("Fetching item descriptions from abilities_english.txt...")
    loc_tokens = _fetch_localization(
        f"{D2VPKR_BASE}/dota/resource/localization/abilities_english.txt"
    )

    item_data = {}

    # Items to skip (internal, recipe, etc.)
    skip_prefixes = ("item_recipe_",)
    hidden_behaviors = {"DOTA_ABILITY_BEHAVIOR_HIDDEN"}

    for item_name, item_info in all_items.items():
        if not item_name.startswith("item_") or not isinstance(item_info, dict):
            continue
        if any(item_name.startswith(p) for p in skip_prefixes):
            continue
        if item_name.endswith("_roshan") or item_name.endswith("_necronomicon"):
            continue

        cost_str = item_info.get("ItemCost", "0")
        try:
            cost = int(cost_str)
        except ValueError:
            cost = 0
        quality = item_info.get("ItemQuality", "")

        # Skip non-purchasable/internal items
        if cost <= 0 and quality != "consumable":
            continue
        if not quality:
            continue
        behavior = item_info.get("AbilityBehavior", "")
        if behavior in hidden_behaviors:
            continue

        # Localization — prefer override for items where Valve's internal
        # name differs from the actual in-game display name
        loc_key = f"DOTA_Tooltip_ability_{item_name}"
        display_name = ITEM_NAME_OVERRIDES.get(
            item_name,
            loc_tokens.get(
                loc_key, item_name.replace("item_", "").replace("_", " ").title()
            ),
        )
        description = loc_tokens.get(f"{loc_key}_Description", "")
        lore = loc_tokens.get(f"{loc_key}_Lore", "")

        # Notes
        notes = []
        for n in range(10):
            note = loc_tokens.get(f"{loc_key}_Note{n}")
            if note:
                notes.append(note)

        # Parse values
        raw_values = item_info.get("AbilityValues", {})
        values = {}
        for vk, vv in raw_values.items():
            if isinstance(vv, dict):
                val = vv.get("value", "")
                if val == "0" or val == "0.0":
                    bonus_keys = [k for k in vv if k.startswith("special_bonus")]
                    val = "variable" if not bonus_keys else val
                values[vk] = val
            else:
                values[vk] = vv

        cooldown = item_info.get("AbilityCooldown", "")
        mana_cost = item_info.get("AbilityManaCost", "")
        shop_tags = item_info.get("ItemShopTags", "")

        item_data[item_name] = {
            "displayName": display_name,
            "description": description,
            "cost": cost,
            "cooldown": cooldown,
            "manaCost": mana_cost,
            "values": values,
            "shopTags": shop_tags,
            "quality": quality,
            "notes": notes,
        }

    # Build display name mapping
    item_displayName_to_id = {
        item["displayName"]: name for name, item in item_data.items()
    }

    Path("data/item_data.json").write_text(json.dumps(item_data))
    Path("data/item_displayName_to_id.json").write_text(
        json.dumps(item_displayName_to_id)
    )
    print(f"Item data gathered ({len(item_data)} items)")
    return item_data


def gather_matchup_data():
    if ignore["matchup_data"] or not hero_data:
        return
    choice = Rank.DIVINE_IMMORTAL
    query = f"""
    {{
    heroStats {{

        matchUp(take: 126, bracketBasicIds: [{choice.value}]) {{
            heroId
            matchCountVs
            matchCountWith
            vs {{
                heroId2
                winCount
                matchCount
                synergy
            }}
            with {{
                heroId2
                winCount
                matchCount
                synergy
            }}
            }}
        }}
    }}
    """
    matchup_data = _stratz(query)["heroStats"]["matchUp"][1:]
    matchup_data = {
        h["heroId"]: {
            "vs": {mu["heroId2"]: mu for mu in h["vs"]},
            "with": {mu["heroId2"]: mu for mu in h["with"]},
            "matchCountVs": h["matchCountVs"],
            "matchCountWith": h["matchCountWith"],
        }
        for h in matchup_data
        if str(h["heroId"]) in hero_data.keys()
    }
    Path("data/matchup_data.json").write_text(json.dumps(matchup_data))
    print(
        f"Found matchup data\nConsistency check: heroes: {len(hero_data)} | matchups: {len(matchup_data)}"
    )


def gather_pos_data():
    if ignore["pos_data"]:
        return
    choice = Rank.DIVINE_IMMORTAL
    try:
        query = f"""
        {{
        heroStats {{
            stats(bracketBasicIds: [{choice.value}], positionIds: [POSITION_1, POSITION_2, POSITION_3, POSITION_4, POSITION_5], groupByPosition: true) {{
            heroId
            position
            winCount
            matchCount
            }}
        }}
        }}
        """
        response = _stratz(query)["heroStats"]["stats"]
        roles = {1: {}, 2: {}, 3: {}, 4: {}, 5: {}}
        i = 0
        while i < len(response):
            roles[int(response[i]["position"][-1])][response[i]["heroId"]] = response[i]
            i += 1
        heroes = set(
            list(roles[1].keys())
            + list(roles[2].keys())
            + list(roles[3].keys())
            + list(roles[4].keys())
            + list(roles[5].keys())
        )
        Path("data/pos_data.json").write_text(json.dumps(roles))
        print(f"Found role data, heroes: {len(heroes)}")
    except Exception as e:
        print(f"Error gathering role data: {e}")
        exit(1)


# ---------------------------------------------------------------------------
#  Aghanim's Scepter/Shard data (OpenDota aghs_desc constants)
# ---------------------------------------------------------------------------


def gather_aghs_data():
    if ignore["aghs_data"] or not hero_data:
        return
    url = "https://api.opendota.com/api/constants/aghs_desc"
    response = get(url)
    aghs_data = {}
    for hero in response.json():
        hid = str(hero["hero_id"])
        hero["hero_name"] = hero_data.get(hid, {}).get("displayName", hid)
        aghs_data[hid] = hero
    print(f"Aghs data gathered: {len(aghs_data)}")
    with open("data/aghs_data.json", "w") as f:
        json.dump(aghs_data, f)


# ---------------------------------------------------------------------------
#  Patch data (already uses d2vpkr)
# ---------------------------------------------------------------------------


def gather_patch_data():
    if ignore["patch_data"] or not hero_data or not item_data:
        return
    url = f"{D2VPKR_BASE}/dota/resource/localization/patchnotes/patchnotes_english.txt"
    response = get(url).text.split("\n")[2:-1]
    GENERAL = "General"
    HEROES = "heroes"
    ABILITIES = "abilities"
    ITEMS = "items"
    hero_keys = {hero["shortName"]: hero["id"] for hero in hero_data.values()}
    item_keys = set(item_data.keys())
    ability_keys = set(
        ability["name"] for hero in hero_data.values() for ability in hero["abilities"]
    )
    patch_cutoff = "7_41"
    flag = False
    patch_data_dict = {
        GENERAL: {},
        HEROES: {
            hero["id"]: {
                "displayName": hero["displayName"],
                ABILITIES: {ability["name"]: {} for ability in hero["abilities"]},
                "hero": {},
            }
            for hero in hero_data.values()
        },
        ITEMS: {name: {} for name in item_data.keys()},
    }

    def _check(path, patch):
        idx = path
        if patch not in idx:
            idx[patch] = ""
        idx[patch] += f"{pair[1]} "

    for line in response:
        l = line.strip().replace('"', "")
        if not flag and l.startswith(f"DOTA_Patch_{patch_cutoff}"):
            flag = True
        if flag:
            pair = l.split("\t\t")
            subject = pair[0]
            if subject[-1].isdigit():
                pair[0] = "_".join(subject.split("_")[:-1])
            info = pair[0].replace("DOTA_Patch_", "").split("_")
            j = 2
            patch = ".".join(info[:2])
            key = ""
            hero_name = ""
            while j < len(info):
                key += f"{info[j]}"
                if key[0] == "_":
                    key = key[1:]
                if not hero_name and key in hero_keys:
                    heroes = patch_data_dict[HEROES][hero_keys[key]]["hero"]
                    if j == len(info) - 1:
                        _check(heroes, patch)
                    if not hero_name:
                        hero_name = key
                        key = ""
                elif GENERAL in key:
                    general = patch_data_dict[GENERAL]
                    _check(general, patch)
                elif key in item_keys:
                    items = patch_data_dict[ITEMS][key]
                    _check(items, patch)
                elif hero_name and key in ability_keys:
                    abilities = patch_data_dict[HEROES][hero_keys[hero_name]][
                        ABILITIES
                    ][key]
                    _check(abilities, patch)
                key += "_"
                j += 1
    print("Patch notes gathered")
    with open("data/patch_data.json", "w") as f:
        json.dump(patch_data_dict, f)


def gather_item_builds():
    """Fetch per-hero item popularity from OpenDota and store as
    data/hero_item_builds.json.  Only keeps items with >5% pick rate per phase
    to filter out noise (e.g. Diffusal on PL post-nerf showing up from 3 games).
    """
    if not hero_data or not item_data:
        print("Skipping item builds — hero_data or item_data not loaded")
        return

    # Build OpenDota numeric item ID -> our display name map
    print("Fetching OpenDota item constants...")
    resp = get("https://api.opendota.com/api/constants/items")
    if resp.status_code != 200:
        print(f"Failed to fetch item constants: HTTP {resp.status_code}")
        return
    od_items = resp.json()
    id_to_name = {}
    for name, info in od_items.items():
        if "id" in info:
            id_to_name[str(info["id"])] = info.get("dname", name)

    MIN_PICK_RATE = 0.05  # 5% threshold
    TOP_N = 8  # max items per phase

    # Load existing data to resume from partial runs
    try:
        with open("data/hero_item_builds.json", "r") as f:
            builds = json.load(f)
        print(f"Resuming — already have {len(builds)} heroes")
    except (FileNotFoundError, json.JSONDecodeError):
        builds = {}

    hero_ids = [h for h in hero_data.keys() if h not in builds]
    print(f"Fetching item builds for {len(hero_ids)} remaining heroes...")
    for i, hero_id in enumerate(hero_ids):
        url = f"https://api.opendota.com/api/heroes/{hero_id}/itemPopularity"
        try:
            resp = get(url)
            if resp.status_code == 429:
                import time

                print("  Rate limited, waiting 60s...")
                time.sleep(60)
                resp = get(url)
            if resp.status_code != 200:
                print(f"  Skipping hero {hero_id}: HTTP {resp.status_code}")
                continue
            data = resp.json()
        except Exception as e:
            print(f"  Error fetching hero {hero_id}: {e}")
            continue
        import time

        time.sleep(1)  # rate limit courtesy

        hero_builds = {}
        for phase, items in data.items():
            if not items:
                continue
            total_games = sum(int(v) for v in items.values())
            if total_games == 0:
                continue
            # Filter to >5% pick rate, sort by games desc, take top N
            filtered = []
            for item_id, games in items.items():
                games = int(games)
                rate = games / total_games
                if rate < MIN_PICK_RATE:
                    continue
                display_name = id_to_name.get(item_id, f"item_{item_id}")
                filtered.append(
                    {
                        "name": display_name,
                        "games": games,
                        "rate": round(rate * 100, 1),
                    }
                )
            filtered.sort(key=lambda x: x["games"], reverse=True)
            phase_key = phase.replace("_items", "")
            hero_builds[phase_key] = {
                "total_games": total_games,
                "items": filtered[:TOP_N],
            }

        if hero_builds:
            builds[str(hero_id)] = hero_builds

        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(hero_ids)} heroes done")

    Path("data/hero_item_builds.json").write_text(json.dumps(builds, indent=2))
    print(f"Item builds gathered for {len(builds)} heroes")
    return builds


def _load(fname):
    try:
        with open(f"data/{fname}.json", "r") as f:
            return json.load(f)
    except Exception:
        ignore[fname] = False


def _exists_checks():
    return (
        _load("hero_data"),
        _load("matchup_data"),
        _load("pos_data"),
        _load("item_data"),
        _load("patch_data"),
    )


if __name__ == "__main__":
    (
        hero_data,
        matchup_data,
        pos_data,
        item_data,
        patch_data,
    ) = _exists_checks()

    hero_data = gather_hero_game_data() or hero_data
    item_data = gather_item_game_data() or item_data
    gather_matchup_data()
    gather_pos_data()
    gather_aghs_data()
    gather_patch_data()
    gather_item_builds()
