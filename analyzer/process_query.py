"""Route a user query (draft-time or general) through the tool-calling agent.

generate_response builds a short context message from the current draft state and
runs the match_review agent loop, which calls tools (patch notes, benchmarks,
etc.) as the query needs. No hardcoded game knowledge: the agent reasons over tool
data, same as the post-game review.
"""

from match_review import run_agent

SYSTEM_QUERY = """You are a Dota 2 assistant for a position-1 (carry) player, answering a question during or about a draft. Use the tools to ground every claim: get_patch_notes for recent hero changes, get_hero_benchmarks for stat baselines. Do not state game facts you have not pulled from a tool; the draft context below tells you which heroes are in play. Answer concisely and concretely. No emojis, no bold."""


def _team_line(label: str, team: dict) -> str:
    heroes = [f"{pos}:{h.name}({h.id})" for pos, h in sorted(team.items())]
    return f"{label}: {', '.join(heroes)}" if heroes else ""


def generate_response(
    query: str,
    team: dict = {},
    enemy_team: dict = {},
    pick: str = "",
    pos: str = "",
    side: str = "",
) -> str:
    lines = [f"QUESTION: {query}"]
    if side:
        lines.append(f"My side: {side}")
    if pos:
        lines.append(f"My position: {pos}")
    if pick:
        lines.append(f"My hero: {pick}")
    ally = _team_line("My team", team)
    enemy = _team_line("Enemy team", enemy_team)
    if ally:
        lines.append(ally)
    if enemy:
        lines.append(enemy)
    user_message = "\n".join(lines)
    return run_agent(SYSTEM_QUERY, user_message)["text"]
