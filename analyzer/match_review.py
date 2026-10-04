"""The post-game review and follow-up chat agent: the tool-calling loop, the
system prompts, and the public review_match / chat_about_match entry points."""

import os
import re
import json

import anthropic
from anthropic.types import MessageParam, ToolResultBlockParam
from dotenv import load_dotenv

import chat_session
from tools import TOOLS, _TOOL_FNS
from utils import ensure_parsed

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
AGENT_MODEL = "claude-sonnet-4-6"

SYSTEM_REVIEW = """You are a Dota 2 post-game coach for a position-1 (carry) player. You write one paragraph, the Read, of a post-game review; code prints the rest of the review around it.

The tool results for this match are already in the conversation, run for you: the profile (get_match_detail, compute_metrics), deaths and gold (get_combat_timings), every teamfight (get_fight_report), aegis and tormentor windows (get_objective_windows), farm (get_farm_pattern), the lane and draft scores, and, on a loss or a good-farm low-damage game, get_timing_windows and get_build_gaps. Do not call those again. Call another tool only for something they do not contain (get_patch_notes, get_break_dispel_targets, get_metric_trend).

Select, do not dump: most of what is in front of you does not belong in the review. How to read it:
- A missed CS checkpoint or a farm drought is a fact for the Read, cited with its fight overlap and gold state.
- A power spike farmed through while the team fought is a candidate reason for a loss. The Read ties it to what the gold did next in one clause; the full note is printed under Item timings.
- Fights: a death is cited by its deaths_detail minute (player_death_minute in the fight row), not the fight's start minute. Cite a fight by its minute with its own numbers (ally deaths, ally_death_spread_s, team_net_gold, player_damage). What a won fight or an aegis was followed by is taken_by_90s_after and buildings_taken_by_holder_team, stated as given, "nothing" included.
- Objectives: get_objective_windows.notable holds the one or two lines worth showing, chosen in code and printed for you; the Read never restates the windows.
- Words that grade are verdicts too: catastrophic, clean, strong, big, dominant, key, hinge, turning point, wipeout. Give the number instead.
- Join facts with "and", "then" or a semicolon. Connectors that assign cause are verdicts: driven by, through, because, cost, erased, gave back, clawed back, tells the story, despite.
- Items: an unused_while_dying item is stated with its fight minute and, in words, whether it had been used in an earlier fight within its cooldown (maybe_on_cooldown); never as a mistake, since disables and uses outside fights are not visible. Never print field names.
- Won: check largest_team_deficit before praising. A real deficit means a comeback win, and get_combat_timings shows what let the enemy in and what turned it. Only a game that was never close gets the short confirmation; do not manufacture a critique of one.
If tool calls return errors, say the match data source (OpenDota) is temporarily unavailable and to retry shortly; do not blame the match id or review without data.

Faithfulness is absolute. Every number you state comes from a tool result, used as given — never computed, re-rounded, or invented, and never moved in time: a deficit reported at minute 55 is the deficit at minute 55, not "the final deficit". Base any claim that the gold swung, collapsed, recovered, or widened on gold_swings and walk its points in order — consecutive points reverse direction, so skipping points to claim one straight slide is fabrication. Cite each death with its own context label and team_gold_adv; never bundle nearby deaths under one label or gold state, and never extend a death's networth_rank or gold beyond its minute ("and stayed there" is fabrication — the samples exist only at the deaths). Positive advantage is favorable, negative unfavorable. No game lore: never claim hero X counters hero Y from your own knowledge — the tool score is the fact, the per-hero why is not yours to add.

Verdicts are fabrication. State what a number is, never what it caused. Any sentence attributing an outcome — "X came from Y", "A, so B", "the problem/issue/reason was (not) X", "X closed it out / threw it", "you could (not) have won" — is fabrication even when every number in it is real; cause is not in the data, in wins as in losses. Put the facts side by side and stop: "the draft scored +1.2; the deficit hit -10975 at 35m" — the player draws the arrow, you never do. Qualities no tool measures (positioning, mechanics, decision-making, map awareness) never appear, as praise or blame. Lay out deaths_detail so the player can judge for themselves: a farmed carry (networth_rank 1-2 of 10) dying caught_alone or first_death_of_teamfight gave something away — cite it plainly, without blame or absolution.

Your value is judgment, and judgment here is selection, not attribution: pick the one or two facts the evidence most points to and put them next to each other. Which facts to show is your call; what caused what is not.

Output. Code prints Result, Deaths, Objectives, Item timings and Pro build reference verbatim from the tool results (result_line, notable_deaths, notable, the timing notes, reference_lines), above and below your paragraph. Your whole reply is the Read paragraph and nothing else: no headings, no separators, no copy of those lines, no second paragraph. The reader has those lines next to your paragraph, so it adds what they do not say and may point at one of them in a clause.
The Read is at most four short sentences, 100 words in total. Each sentence opens with a fact and its number; none opens by characterizing ("The lane was clean", "The gold swing tells the story") and none says what happened after the last sample ("never recovered"). Synthesis; prioritize, do not enumerate. A loss never reads like a win; a hard draft is context, not a verdict. On a loss, end with the open question the evidence cannot settle ("whether cleaner late fights flip a draft this lopsided is not something the numbers can say"), never a verdict on what the problem was. Name items and counts only, never why an item helps.

No emojis, no bold."""


def _strip_emphasis(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text)
    return text


def run_agent(
    system: str,
    user_message: str | None = None,
    history: list[MessageParam] | None = None,
    max_turns: int = 8,
    trace: list[dict] | None = None,
) -> dict:
    """Run the tool-calling loop, optionally continuing a prior conversation.
    history is the messages from earlier turns (None to start fresh); user_message
    is the new turn, or None when history already ends on the turn to answer.
    trace seeds the tool trace with calls already made in code. Returns {"text",
    "tool_trace", "messages", "usage"} where messages is the full updated history
    to persist and pass back next turn and usage the summed token counts."""
    messages: list[MessageParam] = list(history or [])
    if user_message is not None:
        messages.append({"role": "user", "content": user_message})
    trace = list(trace or [])
    usage = {"input_tokens": 0, "output_tokens": 0}
    for _ in range(max_turns):
        resp = client.messages.create(
            model=AGENT_MODEL,
            max_tokens=1500,
            temperature=0,
            system=system,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text")
            return {
                "text": _strip_emphasis(text.strip()),
                "tool_trace": trace,
                "messages": messages,
                "usage": usage,
            }

        results: list[ToolResultBlockParam] = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            try:
                out = _TOOL_FNS[block.name](**block.input)
            except Exception as e:
                out = {"error": str(e)}
            trace.append({"tool": block.name, "input": block.input, "result": out})
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(out),
                }
            )
        messages.append({"role": "user", "content": results})

    return {
        "text": "max turns reached",
        "tool_trace": trace,
        "messages": messages,
        "usage": usage,
    }


_TRIAGE_ALWAYS = (
    "get_match_detail",
    "compute_metrics",
    "get_combat_timings",
    "get_fight_report",
    "get_objective_windows",
    "get_farm_pattern",
    "score_lane_matchup",
    "get_draft_advantage",
)
_TRIAGE_STRATZ = ("get_timing_windows", "get_build_gaps")


def _triage(match_id: int, account_id: int | None) -> list[dict]:
    """Run the review's tool set in code and return it as tool-trace entries.
    The Stratz-backed item tools run only on a loss or a game with good farm
    and a weak hero-damage percentile."""
    args = {"match_id": match_id}
    if account_id is not None:
        args["account_id"] = account_id
    trace = []

    def call(name: str) -> dict:
        try:
            out = _TOOL_FNS[name](**args)
        except Exception as e:
            out = {"error": str(e)}
        trace.append({"tool": name, "input": args, "result": out})
        return out

    results = {name: call(name) for name in _TRIAGE_ALWAYS}
    weak = results["compute_metrics"].get("weak_areas") or []
    lost = results["get_match_detail"].get("won") is False
    if lost or ("hero_damage_per_min" in weak and "gold_per_min" not in weak):
        for name in _TRIAGE_STRATZ:
            call(name)
    return trace


def _triage_messages(ask: str, trace: list[dict]) -> list[MessageParam]:
    """The triage results as a tool-use exchange, so the model, the saved chat
    session and the eval all see them like tool calls the model made."""
    return [
        {"role": "user", "content": ask},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"triage_{i}",
                    "name": step["tool"],
                    "input": step["input"],
                }
                for i, step in enumerate(trace)
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"triage_{i}",
                    "content": json.dumps(step["result"]),
                }
                for i, step in enumerate(trace)
            ],
        },
    ]


def _assemble(read: str, trace: list[dict]) -> str:
    """The review as shown: the model's Read between the blocks code prints
    verbatim from the tool results. Without a result_line (the match data never
    loaded) the model's text is returned as is."""
    by_tool: dict[str, dict] = {}
    for step in trace:
        by_tool.setdefault(step["tool"], step["result"])
    result_line = by_tool.get("get_match_detail", {}).get("result_line")
    if not result_line:
        return read
    read = re.sub(r"^\s*Read:\s*", "", read)
    timing = by_tool.get("get_timing_windows") or {}
    blocks = [
        ("Result", [result_line]),
        ("Read", [read]),
        ("Deaths", by_tool.get("get_combat_timings", {}).get("notable_deaths")),
        ("Objectives", by_tool.get("get_objective_windows", {}).get("notable")),
        (
            "Item timings",
            [m["note"] for m in timing.get("missed") or []]
            or ([timing["verdict"]] if timing.get("verdict") else None),
        ),
        (
            "Pro build reference",
            by_tool.get("get_build_gaps", {}).get("reference_lines"),
        ),
    ]
    return "\n\n".join(
        f"{title}: {lines[0]}" if len(lines) == 1 else f"{title}:\n" + "\n".join(lines)
        for title, lines in blocks
        if lines
    )


UNPARSED_NOTE = (
    "OpenDota has no parsed replay data for this match yet, so a detailed "
    "review is not possible. A parse was just requested — retry in a few "
    "minutes. Matches older than about two weeks may have no replay left "
    "to parse, in which case detailed stats never arrive."
)


def review_match(
    account_id: int | None = None,
    match_id: int | None = None,
    max_turns: int = 8,
) -> dict:
    if account_id is None and match_id is None:
        raise ValueError("provide account_id or match_id")
    if match_id is not None and not ensure_parsed(match_id):
        return {"review": UNPARSED_NOTE, "tool_trace": [], "messages": []}
    if match_id is None:
        match_id = _TOOL_FNS["get_recent_matches"](account_id, 1)["matches"][0][
            "match_id"
        ]
        if not ensure_parsed(match_id):
            return {"review": UNPARSED_NOTE, "tool_trace": [], "messages": []}
    ask = f"Write the Read for match_id {match_id}" + (
        f" for account_id {account_id}." if account_id else "."
    )

    trace = _triage(match_id, account_id)
    result = run_agent(
        SYSTEM_REVIEW,
        history=_triage_messages(ask, trace),
        max_turns=max_turns,
        trace=trace,
    )
    result["text"] = _assemble(result["text"], result["tool_trace"])
    if result["messages"] and result["messages"][-1]["role"] == "assistant":
        # the saved session opens with the review as shown, not the bare Read
        result["messages"][-1] = {
            "role": "assistant",
            "content": [{"type": "text", "text": result["text"]}],
        }
    # Persist the review as the opening of the chat session so a follow-up
    # continues this conversation instead of regenerating the review. A run
    # where get_match_detail never succeeded saw no match data: saving it
    # would poison the session (the UI prefers saved sessions and would keep
    # serving the apology text instead of re-reviewing).
    grounded = any(
        s["tool"] == "get_match_detail" and "error" not in s["result"]
        for s in result["tool_trace"]
    )
    if grounded and account_id is not None:
        chat_session.save(account_id, match_id, result["messages"])
    return {
        "review": result["text"],
        "tool_trace": result["tool_trace"],
        "messages": result["messages"],
        "usage": result.get("usage"),
    }


SYSTEM_CHAT = """You are a Dota 2 coach continuing a conversation about a match you
already reviewed (the review and its tool results are in the history above).
Answer the player's follow-up directly and concisely, in plain prose, not the
review format. Reuse facts already gathered; call a tool only for one you lack.
Faithfulness is absolute: every number comes from a tool result, used as given,
at its own time — base gold-swing claims on gold_swings. If a tool errors, say
the data source (OpenDota) is temporarily unavailable. Never render a fault
verdict in either direction ("purely the draft's fault", "your play was not the
issue"); lay out the evidence, leave the judgment to the player. Never claim one
hero counters another from your own knowledge; for Silver Edge or Nullifier
questions call get_break_dispel_targets and state only the abilities and counts
it returns. For a question about a fight, item use in fights, Roshan, aegis or
tormentor, the facts are in get_fight_report and get_objective_windows; an
unused item is a count, never a mistake. player_items is what the player
actually bought — list it in full when asked, never infer their build from
anything else. Name the items pros build and the player skipped, and
distinct_build if present; never explain why an item helps — you would be
guessing. No emojis, no bold."""


def _block(b, attr, default=None):
    """Field access across both shapes a stored message block can have: SDK
    objects (fresh sessions) and plain dicts (sessions from a JSON store)."""
    return b.get(attr, default) if isinstance(b, dict) else getattr(b, attr, default)


def session_transcript(account_id: int, match_id: int) -> dict | None:
    """The user-visible conversation from a saved session, for the UI: the
    review text and the chat turns after it. None if no session exists. Skips
    the synthetic review request and intermediate tool-calling messages."""
    history = chat_session.load(account_id, match_id)
    if not history:
        return None
    turns = []
    for m in history[1:]:  # history[0] is the synthetic "Review match ..." ask
        content = m["content"]
        if m["role"] == "user":
            if isinstance(content, str):
                turns.append({"role": "user", "text": content})
            continue
        blocks = content if isinstance(content, list) else []
        if any(_block(b, "type") == "tool_use" for b in blocks):
            continue
        text = "".join(
            _block(b, "text", "") for b in blocks if _block(b, "type") == "text"
        ).strip()
        if text:
            turns.append({"role": "assistant", "text": _strip_emphasis(text)})
    if not turns:
        return None
    review = turns.pop(0)["text"] if turns[0]["role"] == "assistant" else None
    return {"review": review, "messages": turns}


def chat_about_match(account_id: int, match_id: int, message: str) -> dict:
    """Answer a follow-up question about a match, continuing the same agent
    conversation. Starts the session with a full review if none exists yet, then
    runs one turn on the question. Persists the updated history."""
    history = chat_session.load(account_id, match_id)
    if history is None:
        review = review_match(account_id=account_id, match_id=match_id)
        history = review["messages"]
        if not history:
            return {"reply": review["review"], "tool_trace": []}
    result = run_agent(SYSTEM_CHAT, message, history=history)
    chat_session.save(account_id, match_id, result["messages"])
    return {"reply": result["text"], "tool_trace": result["tool_trace"]}
