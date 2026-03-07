import json
import os
from enum import Enum
from pathlib import Path
from dotenv import load_dotenv
import cloudscraper
from requests import get

load_dotenv()
token = os.getenv("STRATZ_API_KEY")
scraper = cloudscraper.create_scraper()
Path("data").mkdir(exist_ok=True)


ignore = {
    "hero_data": True,
    "matchup_data": False,
    "pos_data": False,
    "item_data": False,
    "aghs_data": True,
    "patch_data": False,
}


class Rank(Enum):
    HERALD_GUARDIAN = "HERALD_GUARDIAN"
    CRUSADER_ARCHON = "CRUSADER_ARCHON"
    LEGEND_ANCIENT = "LEGEND_ANCIENT"
    DIVINE_IMMORTAL = "DIVINE_IMMORTAL"


def gather_hero_data():
    if ignore["hero_data"]:
        return
    query = """
    {
    constants {
        heroes {
        id
        displayName
        shortName
        roles {
            roleId
        }
        stats {
        attackType
        primaryAttributeEnum
        startingArmor
        moveSpeed
        attackRange
        attackRate
        visionDaytimeRange
        visionNighttimeRange
        }
        abilities {
            ability {
            name
            attributes {
                name
                value
            }
            stat {
                dispellable
            }
            }
        }
        }
    }
    }   
    """
    response = scraper.post(
        "https://api.stratz.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
    )
    if response.status_code == 200:
        hero_data = response.json()
        hero_data = {
            hero["id"]: hero for hero in hero_data["data"]["constants"]["heroes"]
        }
        for hero in hero_data.values():
            abilities = []
            for a in hero["abilities"]:
                ability = {}
                a = a["ability"]
                attributes = a["attributes"]
                attribute_list = []
                if not attributes:
                    continue
                for attribute in attributes:
                    if attribute["value"] == "":
                        continue
                    attribute["name"] = attribute["name"].replace("_", " ").title()
                    attribute_list.append(attribute)
                display_name = (
                    a["name"]
                    .replace(hero["shortName"] + "_", "")
                    .replace("_", " ")
                    .title()
                )
                # not actual display name but whatever
                ability["name"] = a["name"]
                ability["displayName"] = display_name
                ability["attributes"] = attribute_list
                ability["dispellable"] = a["stat"]["dispellable"]
                abilities.append(ability)
            hero_data[hero["id"]]["abilities"] = abilities
            hero["id"] = str(hero["id"])
        hero_displayName_to_id = {
            hero["displayName"]: hero_id for hero_id, hero in hero_data.items()
        }
        Path("data/hero_data.json").open("w").write(json.dumps(hero_data))
        Path("frontend/src/data/hero_data.json").open("w").write(json.dumps(hero_data))
        Path("data/hero_displayName_to_id.json").open("w").write(
            json.dumps(hero_displayName_to_id)
        )
        print(f"Hero data gathered ({len(hero_data)} heroes)")
    else:
        print(f"Heroes data response: HTTP {response.status_code}")
        return None


def gather_matchup_data():
    if ignore["matchup_data"]:
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
    response = scraper.post(
        "https://api.stratz.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
    )
    if response.status_code == 200:
        matchup_data = response.json()["data"]["heroStats"]["matchUp"][1:]
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
        Path("data/matchup_data.json").open("w").write(json.dumps(matchup_data))
        print(
            f"Found hero and matchup data\nConsistency check: heroes: {len(hero_data)} | matchups: {len(matchup_data)}"
        )
    else:
        print(f"Matchup data response: HTTP {response.status_code}")
        return None


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
        response = scraper.post(
            "https://api.stratz.com/graphql",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query},
        )
        response = response.json()["data"]["heroStats"]["stats"]
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
        Path(f"data/pos_data.json").open("w").write(json.dumps(roles))
        print(f"Found role data, heroes: {len(heroes)}")
    except Exception as e:
        print(f"Error gathering role data: {e}")
        exit(1)


def gather_item_data():
    if ignore["item_data"]:
        return
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
    response = scraper.post(
        "https://api.stratz.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
    )

    if response.status_code == 200:
        items = response.json()["data"]["constants"]["items"]
        items = {str(item["id"]): item for item in items}
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
            "Bfury": "Battle Fury",
        }
        item_data = {}
        for item in items.values():
            if (
                not item["name"].endswith("_roshan")
                and not item["name"].endswith("_necronomicon")
                and item["name"] not in invalid_items
                and item["stat"] is not None
                and item["stat"]["cost"] > 30
                and item["stat"]["quality"] is not None
            ):
                # also not display name
                display_name = " ".join(item["name"].split("_")[1:]).title()
                item_data[str(item["id"])] = item
                item_data[str(item["id"])]["displayName"] = rename_items.get(
                    display_name, display_name
                )
        item_displayName_to_id = {
            item["displayName"]: str(item["id"]) for item in item_data.values()
        }
        print(f"{len(item_data)} (items)")
        Path("data/item_data.json").open("w").write(json.dumps(item_data))
        Path("data/item_displayName_to_id.json").open("w").write(
            json.dumps(item_displayName_to_id)
        )
        print(f"Found item data")
    else:
        print(f"Items data response: HTTP {response.status_code}")
        return None


def gather_aghs_data():
    if ignore["aghs_data"]:
        return
    url = "https://api.opendota.com/api/constants/aghs_desc"
    response = get(url)
    aghs_data = {}
    for hero in response.json():
        hero["hero_name"] = hero_data[str(hero["hero_id"])]["displayName"]
        aghs_data[hero["hero_id"]] = hero

    print(f"Aghs data gathered: {len(aghs_data)}")
    with open("data/aghs_data.json", "w") as f:
        json.dump(aghs_data, f)


def gather_patch_data():
    if ignore["patch_data"]:
        return
    url = "https://raw.githubusercontent.com/dotabuff/d2vpkr/refs/heads/master/dota/resource/localization/patchnotes/patchnotes_english.txt"
    response = get(url).text.split("\n")[2:-1]
    GENERAL = "General"
    HEROES = "heroes"
    ABILITIES = "abilities"
    ITEMS = "items"
    hero_keys = {hero["shortName"]: hero["id"] for hero in hero_data.values()}
    item_keys = set([item["name"] for item in item_data.values()])
    ability_keys = set(
        ability["name"] for hero in hero_data.values() for ability in hero["abilities"]
    )
    patch_cutoff = "7_38"
    flag = False
    patch_data_dict = {
        GENERAL: {},
        HEROES: {
            hero["id"]: {
                ABILITIES: {ability["name"]: {} for ability in hero["abilities"]},
                "hero": {},
            }
            for hero in hero_data.values()
        },
        ITEMS: {item["name"]: {} for item in item_data.values()},
    }

    def check(path, patch):
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
                        check(heroes, patch)
                    if not hero_name:
                        hero_name = key
                        key = ""
                elif GENERAL in key:
                    general = patch_data_dict[GENERAL]
                    check(general, patch)
                elif key in item_keys:
                    items = patch_data_dict[ITEMS][key]
                    check(items, patch)
                elif hero_name and key in ability_keys:
                    abilities = patch_data_dict[HEROES][hero_keys[hero_name]][
                        ABILITIES
                    ][key]
                    check(abilities, patch)
                key += "_"
                j += 1
    print("Patch notes gathered")
    with open("data/patch_data.json", "w") as f:
        json.dump(patch_data_dict, f)


def _load(fname):
    try:
        with open(f"data/{fname}.json", "r") as f:
            return json.load(f)
    except:
        ignore[fname] = False


def _exists_checks():
    return (
        _load("hero_data"),
        _load("matchup_data"),
        _load("pos_data"),
        _load("item_data"),
        _load("aghs_data"),
        _load("patch_data"),
    )


if __name__ == "__main__":
    (
        hero_data,
        matchup_data,
        pos_data,
        item_data,
        aghs_data,
        patch_data,
    ) = _exists_checks()
    gather_hero_data()
    gather_matchup_data()
    gather_pos_data()
    gather_item_data()
    gather_aghs_data()
    gather_patch_data()
