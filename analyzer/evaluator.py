from counters import get_counter_score, get_synergy_score
import json

with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)
with open("data/matchup_data.json", "r") as f:
    matchup_data = json.load(f)
with open("data/pos_data.json", "r") as f:
    pos_dict = json.load(f)

TOTAL_MATCHES = sum([v["matchCountVs"] for v in matchup_data.values()]) / 5


class Hero:
    def __init__(self, name: str, id: str, score: float, pos: int):
        self.name = name
        self.id = id
        self.score = score
        self.pos = pos


def rank_picks(team, enemy_team, pos):
    scores = {}
    team_ids = set(h.id for h in team.values())
    enemy_team_ids = set(h.id for h in enemy_team.values())
    for hero_id in matchup_data.keys():
        if hero_id in team_ids or hero_id in enemy_team_ids:
            continue
        score = evaluate_hero(hero_id, team, enemy_team, str(pos))
        if score != -1000:
            scores[hero_id] = score
    ranked_scores = list(reversed(sorted(scores.items(), key=lambda x: x[1])))
    ranked_scores = [
        Hero(hero_data[hero_id]["displayName"], hero_id, score, str(pos))
        for hero_id, score in ranked_scores
    ]
    return ranked_scores


def evaluate_hero(hero_id, team, enemy_team, pos, bypass_check=False):
    if not bypass_check and not _is_viable(hero_id, pos):
        return -1000
    score = 100 * (
        pos_dict[pos][hero_id]["winCount"] / pos_dict[pos][hero_id]["matchCount"] - 0.5
    )
    for ally in team.values():
        ss = get_synergy_score(hero_id, ally.id, matchup_data, pos, ally.pos, pos_dict)
        score += ss / 2
    for enemy in enemy_team.values():
        cs = get_counter_score(
            hero_id,
            enemy.id,
            matchup_data,
            pos,
            enemy.pos,
            pos_dict,
        )
        score += cs - get_counter_score(
            enemy.id,
            hero_id,
            matchup_data,
            enemy.pos,
            pos,
            pos_dict,
        )
    return score


def _is_viable(hero_id, pos):
    if matchup_data[hero_id]["matchCountVs"] / TOTAL_MATCHES < 0.01:
        return None
    match_count_sum = 0
    for p in pos_dict.keys():
        match_count_sum += pos_dict[str(p)][hero_id]["matchCount"]
    return pos_dict[pos][hero_id]["matchCount"] >= match_count_sum / len(pos_dict)


def score_teams(team, enemy_team):
    radiant_score = 0
    dire_score = 0

    team_list = team.values()
    enemy_list = enemy_team.values()
    debug = {}
    for hero in team_list:
        score = evaluate_hero(
            hero.id,
            team,
            enemy_team,
            hero.pos,
            bypass_check=True,
        )
        radiant_score += score
        debug[hero.id] = score
    for hero in enemy_list:
        score = evaluate_hero(
            hero.id,
            enemy_team,
            team,
            hero.pos,
            bypass_check=True,
        )
        dire_score += score
        debug[hero.id] = score
    return radiant_score, dire_score, radiant_score - dire_score
