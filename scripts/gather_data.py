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
            }
            }
        }
        }
    }
    }   
    """
    hero_stats_response = scraper.post(
        "https://api.stratz.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
    )
    if hero_stats_response.status_code == 200:
        hero_stats = hero_stats_response.json()
        hero_stats = {
            hero["id"]: hero for hero in hero_stats["data"]["constants"]["heroes"]
        }
        Path("data/hero_data.json").open("w").write(json.dumps(hero_stats))
        return hero_stats
    else:
        print(f"Heroes data response: HTTP {hero_stats_response.status_code}")
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
