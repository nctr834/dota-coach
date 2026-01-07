from counters import get_counter_score, get_synergy_score
import math

import json

with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)
with open("data/matchup_data.json", "r") as f:
    matchup_data = json.load(f)
with open("data/role_data_POSITION_1.json", "r") as f:
    pos1_data = json.load(f)
with open("data/role_data_POSITION_2.json", "r") as f:
    pos2_data = json.load(f)
with open("data/role_data_POSITION_3.json", "r") as f:
    pos3_data = json.load(f)
with open("data/role_data_POSITION_4.json", "r") as f:
    pos4_data = json.load(f)
with open("data/role_data_POSITION_5.json", "r") as f:
    pos5_data = json.load(f)
pos_dict = {
    "POSITION_1": pos1_data,
    "POSITION_2": pos2_data,
    "POSITION_3": pos3_data,
    "POSITION_4": pos4_data,
    "POSITION_5": pos5_data,
}
pos_set = set(pos_dict.keys())


total_matches = sum(matchup_data[hero]["matchCountVs"] for hero in matchup_data)
avg_matches = total_matches / len(matchup_data)
print(f"Total matches: {total_matches}")
print(f"Average matches: {avg_matches}")
hero_winrates = {
    hero: (
        sum(
            matchup_data[hero]["vs"][enemy]["winCount"]
            for enemy in matchup_data[hero]["vs"]
        ),
        matchup_data[hero]["matchCountVs"],
    )
    for hero in matchup_data
}
print(f"Hero winrates: {hero_winrates['elder titan'][0]}")


def evaluate_picks(team, enemy_team, matchup_data, pos, pos_set, pos_dict):
    scores = {}
    for hero in matchup_data.keys():
        if (
            hero in team
            or hero in enemy_team
            or not is_viable_pos(matchup_data[hero]["heroId"], pos, pos_set, pos_dict)
            or hero_winrates[hero][1] < avg_matches / 4
        ):
            continue
        if hero == "troll warlord":
            print("Troll Warlord")
        score = 0
        for enemy in enemy_team.keys():
            score -= get_counter_score(enemy, hero, matchup_data)
        for ally in team.keys():
            score += get_synergy_score(hero, ally, matchup_data)
        score = score / (len(enemy_team) + len(team))
        scores[hero] = score
    return list(reversed(sorted(scores.items(), key=lambda x: x[1])))[:10]


def is_viable_pos(heroId, pos, pos_set, pos_dict):
    sum = 0
    for p in pos_set:
        sum += pos_dict[p][str(heroId)]
    avg = sum / len(pos_set)
    if pos_dict[pos][str(heroId)] > avg:
        # print(
        #     f"{hero_data[str(heroId)]['displayName']} is viable for {pos} with {pos_dict[pos][str(heroId)]} matches\n\tavg: {avg} | pos1: {pos_dict["POSITION_1"][str(heroId)]} | pos2: {pos_dict["POSITION_2"][str(heroId)]} | pos3: {pos_dict["POSITION_3"][str(heroId)]} | pos4: {pos_dict["POSITION_4"][str(heroId)]} | pos5: {pos_dict["POSITION_5"][str(heroId)]}"
        # )
        return True
    return False


if __name__ == "__main__":
    pos = "POSITION_1"
    print(
        evaluate_picks(
            {"axe": 3, "puck": 2},
            {"anti-mage": 1, "storm spirit": 2, "enigma": 3, "dazzle": 4},
            matchup_data,
            pos,
            pos_set,
            pos_dict,
        )
    )
