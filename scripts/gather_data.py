import json
import os
from re import search
import regex as re
from enum import Enum
from pathlib import Path
from dotenv import load_dotenv
import cloudscraper

load_dotenv()
token = os.getenv("STRATZ_API_KEY")
scraper = cloudscraper.create_scraper()

# Create data directory if it doesn't exist
Path("data").mkdir(exist_ok=True)


class Rank(Enum):
    HERALD_GUARDIAN = "HERALD_GUARDIAN"
    CRUSADER_ARCHON = "CRUSADER_ARCHON"
    LEGEND_ANCIENT = "LEGEND_ANCIENT"
    DIVINE_IMMORTAL = "DIVINE_IMMORTAL"


def gather_hero_data():
    query = """
    {
    constants {
        heroes {
        id
        displayName
        shortName
        abilities {
            ability {
            name
            attributes {
                name
                value
            }
            stat {
                dispellable
                duration
                hasScepterUpgrade
                hasShardUpgrade
                isGrantedByShard
            }
            }
        }
        }
    }
    }   
    """
    hero_data_response = scraper.post(
        "https://api.stratz.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
    )
    if hero_data_response.status_code == 200:
        hero_data = hero_data_response.json()
        hero_data = {
            hero["id"]: hero for hero in hero_data["data"]["constants"]["heroes"]
        }
        for hero_id, hero in hero_data.items():
            abilities = {}
            for ability in hero["abilities"]:
                attributes = []
                if not ability["ability"]["attributes"]:  # generic_hidden
                    continue
                for attribute in ability["ability"]["attributes"]:
                    if (
                        attribute["value"] == ""
                        and "scepter" not in attribute["name"]
                        and "shard" not in attribute["name"]
                    ):
                        continue
                    attributes.append(attribute)
                ability["ability"]["attributes"] = attributes
                ability_name = ability["ability"]["name"]
                short_name = (
                    ability_name.replace(hero["shortName"] + "_", "")
                    .replace("_", " ")
                    .title()
                )
                abilities[short_name] = ability
            hero_data[hero_id]["abilities"] = abilities
        Path("data/hero_data.json").open("w").write(json.dumps(hero_data))
        return hero_data
    else:
        print(f"Heroes data response: HTTP {hero_data_response.status_code}")
        return None


def gather_matchup_data(hero_data):
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
    matchup_data = scraper.post(
        "https://api.stratz.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
    )
    if matchup_data.status_code == 200:
        matchup_data = matchup_data.json()["data"]["heroStats"]["matchUp"][1:]
        matchup_data = {
            hero_data[m["heroId"]]["shortName"].lower(): {
                "heroId": m["heroId"],
                "matchCountVs": m["matchCountVs"],
                "matchCountWith": m["matchCountWith"],
                "vs": {
                    hero_data[r["heroId2"]]["shortName"].lower(): r for r in m["vs"]
                },
                "with": {
                    hero_data[r["heroId2"]]["shortName"].lower(): r for r in m["with"]
                },
            }
            for m in matchup_data
            if m["heroId"] in hero_data
        }
        Path("data/matchup_data.json").open("w").write(json.dumps(matchup_data))
        print(
            f"Found hero and matchup data\nConsistency check: heroes: {len(hero_data)} | matchups: {len(matchup_data)}"
        )
        return matchup_data
    else:
        print(f"Matchup data response: HTTP {matchup_data.status_code}")
        return None


def gather_pos_data():
    choice = Rank.DIVINE_IMMORTAL
    positions = ["POSITION_1", "POSITION_2", "POSITION_3", "POSITION_4", "POSITION_5"]
    try:
        for p in positions:
            query = f"""
            {{
            heroStats {{
                stats(bracketBasicIds: [{choice.value}], positionIds: [{p}]) {{
                heroId
                matchCount
                }}
            }}
            }}
            """
            roles = scraper.post(
                "https://api.stratz.com/graphql",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query},
            )
            roles = roles.json()["data"]["heroStats"]["stats"]
            roles = {role["heroId"]: role["matchCount"] for role in roles}
            for id in hero_data:
                if id not in roles:
                    roles[id] = 0
            Path(f"data/role_data_{p}.json").open("w").write(json.dumps(roles))
            print(f"Found role data for {p}, roles: {len(roles)}")
    except Exception as e:
        print(f"Error gathering role data: {e}")
        exit(1)


def gather_item_data():
    query = """
    {
    constants {
        items{
        id
        name
        attributes {
            name
            value
        }
        stat{
            cost
            quality
        }
        }
    }
    }
    """
    items = scraper.post(
        "https://api.stratz.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
    )

    if items.status_code == 200:
        items = items.json()["data"]["constants"]["items"]
        items = {item["name"]: item for item in items}
        invalid_items = {
            "item_samurai_tabi",
            "item_hermes_sandals",
            "item_witches_switch",
            "item_aetherial_halo",
            "item_wraith_pact",
            "item_cheese",
            "item_stout_shield",
            "item_ancient_janggo",
            "item_tome_of_knowledge",
            "item_refresher_shard",
            "item_courier",
            "item_flying_courier",
            "item_grandmasters_glaive",
            "item_specialists_array",
        }
        items = {
            " ".join(item["name"].split("_")[1:]).title(): item
            for item in items.values()
            if not item["name"].endswith("_roshan")
            if not item["name"].endswith("_necronomicon")
            if item["name"] not in invalid_items
            if item["stat"] is not None
            if item["stat"]["cost"] > 30
            if item["stat"]["quality"] is not None
        }
        rename_items = {
            "Devastator": "Parasma",
            "Angels Demise": "Khanda",
            "Gungir": "Gleipnir",
            "Lifesteal": "Morbid Mask",
            "Sphere": "Linkens Sphere",
            "Assault": "Assault Cuirass",
            "Lesser Crit": "Crystalys",
            "Greater Crit": "Daedalus",
            "Invis Sword": "Shadow Blade",
        }
        items = {rename_items.get(k, k): v for k, v in items.items()}
        print(f"{len(items)} (items)")
        Path("data/item_data.json").open("w").write(json.dumps(items))
        print(f"Found item data")
        return items
    else:
        print(f"Items data response: HTTP {items.status_code}")
        return None


if __name__ == "__main__":
    try:
        hero_data = gather_hero_data()
        matchup_data = gather_matchup_data(
            hero_data
        )  # pass hero_data to avoid potential null matchup
        roles = gather_pos_data()
        items = gather_item_data()

    except Exception as e:
        print(f"Error gathering data: {e}")
        exit(1)
