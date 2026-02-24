from counters import get_counter_score, get_synergy_score
import json

with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)
with open("data/matchup_data.json", "r") as f:
    matchup_data = json.load(f)


total_matches = sum(matchup_data[hero]["matchCountVs"] for hero in matchup_data)
avg_matches = total_matches / len(matchup_data)
hero_sums_vs = {
    hero: (
        sum(
            matchup_data[hero]["vs"][enemy]["winCount"]
            for enemy in matchup_data[hero]["vs"]
        ),
        matchup_data[hero]["matchCountVs"],
    )
    for hero in matchup_data
}

hero_sums_with = {
    hero: (
        sum(
            matchup_data[hero]["with"][enemy]["winCount"]
            for enemy in matchup_data[hero]["with"]
        ),
        matchup_data[hero]["matchCountWith"],
    )
    for hero in matchup_data
}


class Hero:
    def __init__(self, name, id, score):
        self.name = name
        self.id = id
        self.score = score


def rank_picks(
    team,
    enemy_team,
    pos,
    pos_dict,
):
    scores = {}
    for hero in matchup_data.keys():
        scores[hero] = evaluate_hero(hero, team, enemy_team, pos, pos_dict)

    ranked_scores = list(reversed(sorted(scores.items(), key=lambda x: x[1])))[:5]
    ranked_scores = [
        Hero(hero, matchup_data[hero]["heroId"], score) for hero, score in ranked_scores
    ]
    return ranked_scores


def evaluate_hero(hero, team, enemy_team, pos, pos_dict, bypass_check=False):
    if not bypass_check and (
        hero in team.values()
        or hero in enemy_team.values()
        or not _is_viable(hero, pos, pos_dict)
    ):
        return -1000
    score = 100 * (
        (hero_sums_vs[hero][0] + hero_sums_with[hero][0])
        / (hero_sums_vs[hero][1] + hero_sums_with[hero][1])
        - 0.5
    )
    for ally in team.values():
        score += get_synergy_score(hero, ally.name, matchup_data)
    for enemy in enemy_team.values():
        score += get_counter_score(hero, enemy.name, matchup_data) - get_counter_score(
            enemy.name, hero, matchup_data
        )
    return score


def _is_viable(hero, pos, pos_dict):
    if matchup_data[hero]["matchCountVs"] / 5 < total_matches / 5 * 0.005:
        return False
    s = 0
    for p in pos_dict.keys():
        s += pos_dict[p][str(matchup_data[hero]["heroId"])]
    return pos_dict[pos][str(matchup_data[hero]["heroId"])] >= s / len(pos_dict)


def score_teams(team, enemy_team):
    radiant_score = 0
    dire_score = 0

    team_list = team.values()
    enemy_list = enemy_team.values()

    for hero in team_list:
        radiant_score += evaluate_hero(
            hero.name,
            team,
            enemy_team,
            "",
            {},
            bypass_check=True,
        )
    for hero in enemy_list:
        dire_score += evaluate_hero(
            hero.name,
            enemy_team,
            team,
            "",
            {},
            bypass_check=True,
        )

    return radiant_score, dire_score, radiant_score - dire_score
