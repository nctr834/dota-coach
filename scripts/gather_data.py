import json
import os
from enum import Enum
from dotenv import load_dotenv
import cloudscraper

load_dotenv()
token = os.getenv("STRATZ_API_KEY")
scraper = cloudscraper.create_scraper()


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
            stat {
                behavior
                unitTargetType
                unitTargetTeam
                unitDamageType
                spellImmunity
                isUltimate
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
        hero_by_id = {
            hero["id"]: hero for hero in hero_stats["data"]["constants"]["heroes"]
        }
        hero_by_name = {
            hero["displayName"].lower(): hero["id"]
            for hero in hero_stats["data"]["constants"]["heroes"]
        }
        with open("hero_data.json", "w") as f:
            json.dump(hero_by_id, f)
        print(f"Found {len(hero_stats['data']['constants']['heroes'])} heroes")
        return hero_by_id, hero_by_name
    else:
        print(f"Heroes data response: HTTP {hero_stats_response.status_code}")
        return None, None


def gather_matchup_data(ids):
    choice = Rank.DIVINE_IMMORTAL
    query = f"""
    {{
    heroStats {{
        matchUp(take: 130, bracketBasicIds: [{choice.value}]) {{
            heroId
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
    matchups = scraper.post(
        "https://api.stratz.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
    )
    if matchups.status_code == 200:
        matchups = matchups.json()["data"]["heroStats"]["matchUp"][1:]
        matchups = {m["heroId"]: m for m in matchups if m["heroId"] in ids}
        with open("matchup_data.json", "w") as f:
            json.dump(matchups, f)
        print(f"Found {len(matchups)} matchups")
        return matchups
    else:
        print(f"Matchup data response: HTTP {matchups.status_code}")
        return None


if __name__ == "__main__":
    id_to_name, name_to_id = gather_hero_data()
    # have to pass this since there is an unused id returned in heroStats query
    ids = id_to_name.keys()
    matchups = gather_matchup_data(ids)

    def name_to_idx(name):
        return str(name_to_id[name.lower()])

    with open("hero_data.json", "r") as f:
        heroes = json.load(f)
    print(heroes[name_to_idx("anti-mage")])
