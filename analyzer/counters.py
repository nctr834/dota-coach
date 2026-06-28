def get_counter_score(hero_id, enemy_id, matchup_data, pos, enemy_pos, pos_dict):
    if hero_id == enemy_id:
        return 0
    PRIOR_MATCHES = 100
    PRIOR_WR = 0.5

    wins = matchup_data[hero_id]["vs"][enemy_id]["winCount"] / 5
    matches = matchup_data[hero_id]["vs"][enemy_id]["matchCount"] / 5
    vs_wr = (wins + PRIOR_MATCHES * PRIOR_WR) / (matches + PRIOR_MATCHES)
    hero_at_role = pos_dict[pos][hero_id]
    enemy_at_role = pos_dict[enemy_pos][enemy_id]
    hero_base = hero_at_role["winCount"] / hero_at_role["matchCount"]
    enemy_base = enemy_at_role["winCount"] / enemy_at_role["matchCount"]
    expected = 0.5 + (hero_base - 0.5) - (enemy_base - 0.5)
    score = 100 * ((expected + vs_wr) / 2 - 0.5) / 5
    return score


def get_synergy_score(hero_id, ally_id, matchup_data, pos, ally_pos, pos_dict):
    if hero_id == ally_id:
        return 0
    PRIOR_MATCHES = 100
    PRIOR_WR = 0.5

    wins = matchup_data[hero_id]["with"][ally_id]["winCount"] / 4
    matches = matchup_data[hero_id]["with"][ally_id]["matchCount"] / 4
    with_wr = (wins + PRIOR_MATCHES * PRIOR_WR) / (matches + PRIOR_MATCHES)
    hero_at_role = pos_dict[pos][hero_id]
    ally_at_role = pos_dict[ally_pos][ally_id]
    hero_base = hero_at_role["winCount"] / hero_at_role["matchCount"]
    ally_base = ally_at_role["winCount"] / ally_at_role["matchCount"]
    expected = 0.5 + (hero_base - 0.5) + (ally_base - 0.5)
    score = 100 * ((expected + with_wr) / 2 - 0.5) / 4
    return score
