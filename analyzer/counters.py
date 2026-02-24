import json

with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)
with open("data/matchup_data.json", "r") as f:
    matchup_data = json.load(f)


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


def get_counter_score(hero, enemy, matchup_data):
    if hero == enemy:
        return 0
    PRIOR_MATCHES = 100
    PRIOR_WR = 0.5

    wins = matchup_data[hero]["vs"][enemy]["winCount"]
    matches = matchup_data[hero]["vs"][enemy]["matchCount"]

    vs_wr = (wins + PRIOR_MATCHES * PRIOR_WR) / (matches + PRIOR_MATCHES)
    hero_base = hero_sums_vs[hero][0] / hero_sums_vs[hero][1]
    enemy_base = hero_sums_vs[enemy][0] / hero_sums_vs[enemy][1]
    expected = (hero_base + enemy_base) / 2
    return 100 * (vs_wr - expected)


def get_synergy_score(hero, ally, matchup_data):
    if hero == ally:
        return 0
    PRIOR_MATCHES = 100
    PRIOR_WR = 0.5

    wins = matchup_data[hero]["with"][ally]["winCount"]
    matches = matchup_data[hero]["with"][ally]["matchCount"]

    with_wr = (wins + PRIOR_MATCHES * PRIOR_WR) / (matches + PRIOR_MATCHES)
    hero_base = hero_sums_with[hero][0] / hero_sums_with[hero][1]
    ally_base = hero_sums_with[ally][0] / hero_sums_with[ally][1]
    expected = (hero_base + ally_base) / 2
    return 100 * (with_wr - expected)
