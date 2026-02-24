import json
from evaluator import rank_picks, score_teams

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


def get_input(team, enemy_team, side, i, hero=None, role=None):
    # print(f"\n{side} pick - {i + 1} | current picks: {team}")
    suggestions = rank_picks(team, enemy_team, f"POSITION_{role}", pos_dict)
    # print(f"suggestions: {suggestions}")
    hero = 0
    team[role] = suggestions[hero]
    # print(f"{side} pick - {i + 1} | current picks: {team}")


def compose():
    radiant = {}
    dire = {}
    for i in range(5):
        get_input(radiant, dire, "radiant", i, hero=None, role=i + 1)
        get_input(dire, radiant, "dire", i, hero=None, role=i + 1)
    radiant_score, dire_score, delta = score_teams(radiant, dire)
    return radiant, dire, radiant_score, dire_score, delta


radiant, dire, radiant_score, dire_score, delta = compose()
print(f"\nradiant: {radiant}\ndire: {dire}")
print(f"radiant_score: {radiant_score}\ndire_score: {dire_score}")
print(f"delta: {delta}")
