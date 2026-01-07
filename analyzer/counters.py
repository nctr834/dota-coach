import json

hero_data = None
matchup_data = None

with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)
with open("data/matchup_data.json", "r") as f:
    matchup_data = json.load(f)


hero_sums = {
    hero: (
        sum(
            matchup_data[hero]["vs"][enemy]["winCount"]
            for enemy in matchup_data[hero]["vs"]
        ),
        matchup_data[hero]["matchCountVs"],
    )
    for hero in matchup_data
}


def get_counter_score(hero, enemy, matchup_data):
    return 100 * (
        (
            matchup_data[hero]["vs"][enemy]["winCount"]
            / matchup_data[hero]["vs"][enemy]["matchCount"]
            - (
                (hero_sums[hero][0] + hero_sums[enemy][0])
                / (hero_sums[hero][1] + hero_sums[enemy][1])
            )
        )
    )


def get_synergy_score(hero, ally, matchup_data):
    return 100 * (
        (
            matchup_data[hero]["with"][ally]["winCount"]
            / matchup_data[hero]["with"][ally]["matchCount"]
            - (
                (hero_sums[hero][0] + hero_sums[ally][0])
                / (hero_sums[hero][1] + hero_sums[ally][1])
            )
        )
    )


if __name__ == "__main__":
    print("Counter score:", get_counter_score("anti-mage", "axe"))
    print("Counter score:", get_counter_score("axe", "anti-mage"))
    print("Counter score:", get_counter_score("anti-mage", "puck"))
    print("Counter score:", get_counter_score("puck", "anti-mage"))
    print("Counter score:", get_counter_score("storm spirit", "axe"))
    print("Counter score:", get_counter_score("storm spirit", "puck"))
    print("Synergy score:", get_synergy_score("anti-mage", "storm spirit"))
    print("Synergy score:", get_synergy_score("axe", "puck"))
    print("Hero Win Rate:", hero_winrates["anti-mage"])
