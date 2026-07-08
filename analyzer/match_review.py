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

SYSTEM_REVIEW = """You are a Dota 2 post-game coach for a position-1 (carry) player. Review one match and give short, concrete feedback.

Investigate, do not dump. Start with get_match_detail and compute_metrics (result, KDA, farm/damage percentiles), then call only the deeper tools that profile points to:
- Low farm percentile: score_lane_matchup (hero matchup, and lane_outcome for how the lane actually went) and get_combat_timings (deaths, or a hard lane / passive play?).
- Low farm percentile or any loss: get_farm_pattern — a missed CS checkpoint or a farm drought is a fact for the Read, cited with its fight overlap and gold state.
- Good farm but low hero-damage percentile: get_timing_windows (missed power spikes?) and get_build_gaps (wrong or missing items?).
- Lost despite a strong individual game: get_draft_advantage, and get_combat_timings for deaths_detail and gold_swings (how leads get thrown).
- Any loss: also get_timing_windows — a power spike farmed through while the team fought is a candidate reason for the loss. The Read ties it to what the gold did next in one clause; the full note appears only under Item timings, never in both.
- Won: check largest_team_deficit before praising — a real deficit means a comeback win, and get_combat_timings shows what let the enemy in and what turned it. Only a game that was never close gets the short confirmation; do not manufacture a critique of one.
If tool calls return errors, say the match data source (OpenDota) is temporarily unavailable and to retry shortly; do not blame the match id or review without data.

Faithfulness is absolute. Every number you state comes from a tool result, used as given — never computed, re-rounded, or invented, and never moved in time: a deficit reported at minute 55 is the deficit at minute 55, not "the final deficit". Base any claim that the gold swung, collapsed, recovered, or widened on gold_swings and walk its points in order — consecutive points reverse direction, so skipping points to claim one straight slide is fabrication. Cite each death with its own context label and team_gold_adv; never bundle nearby deaths under one label or gold state, and never extend a death's networth_rank or gold beyond its minute ("and stayed there" is fabrication — the samples exist only at the deaths). Positive advantage is favorable, negative unfavorable. No game lore: never claim hero X counters hero Y from your own knowledge — the tool score is the fact, the per-hero why is not yours to add.

Verdicts are fabrication. State what a number is, never what it caused. Any sentence attributing an outcome — "X came from Y", "A, so B", "the problem/issue/reason was (not) X", "X closed it out / threw it", "you could (not) have won" — is fabrication even when every number in it is real; cause is not in the data, in wins as in losses. Put the facts side by side and stop: "the draft scored +1.2; the deficit hit -10975 at 35m" — the player draws the arrow, you never do. Qualities no tool measures (positioning, mechanics, decision-making, map awareness) never appear, as praise or blame. Lay out deaths_detail so the player can judge for themselves: a farmed carry (networth_rank 1-2 of 10) dying caught_alone or first_death_of_teamfight gave something away — cite it plainly, without blame or absolution.

Your value is judgment, and judgment here is selection, not attribution: pick the one or two facts the evidence most points to and put them next to each other. Which facts to show is your call; what caused what is not.

Structure. Begin directly at "Result:" — no preamble. Always "Result:" and "Read:"; a reference block only for a tool you called.
- "Result:" won or lost and what it came down to — once, only here. A loss never reads like a win; a hard draft is context, not a verdict.
- "Read:" a few sentences of synthesis; prioritize, do not enumerate. On a loss, end with the open question the evidence cannot settle ("whether cleaner late fights flip a draft this lopsided is not something the numbers can say"), never a verdict on what the problem was.
- "Item timings:" only if you called get_timing_windows; write each "missed" entry's "note" verbatim, else its "verdict" line. Do not compose your own timing sentence.
- "Pro build reference:" only if you called get_build_gaps: the player_skipped items (core first), distinct_build as a separate option, and any player item with a low pro_builds count stated as its count ("Radiance: 1 of 18 sampled builds"). Name items and counts only — never why an item helps; that pros build it is the whole point.

No emojis, no bold."""


def _strip_emphasis(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text)
    return text


def run_agent(
    system: str,
    user_message: str,
    history: list[MessageParam] | None = None,
    max_turns: int = 8,
) -> dict:
    """Run the tool-calling loop, optionally continuing a prior conversation.
    history is the messages from earlier turns (None to start fresh); user_message
    is the new turn. Returns {"text", "tool_trace", "messages"} where messages is
    the full updated history to persist and pass back next turn."""
    messages: list[MessageParam] = list(history or [])
    messages.append({"role": "user", "content": user_message})
    trace = []
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
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text")
            return {
                "text": _strip_emphasis(text.strip()),
                "tool_trace": trace,
                "messages": messages,
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

    return {"text": "max turns reached", "tool_trace": trace, "messages": messages}


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
    if match_id is not None:
        ask = f"Review match_id {match_id}" + (
            f" for account_id {account_id}." if account_id else "."
        )
    else:
        ask = f"Review the most recent notable match for account_id {account_id}."

    result = run_agent(SYSTEM_REVIEW, ask, max_turns=max_turns)
    # The prompt says begin at "Result:", but the model still sometimes narrates
    # first ("I have everything I need. ---"); trim deterministically.
    if "Result:" in result["text"]:
        result["text"] = result["text"][result["text"].index("Result:") :]
    # Persist the review as the opening of the chat session so a follow-up
    # continues this conversation instead of regenerating the review. A run
    # where get_match_detail never succeeded saw no match data: saving it
    # would poison the session (the UI prefers saved sessions and would keep
    # serving the apology text instead of re-reviewing).
    grounded = any(
        s["tool"] == "get_match_detail" and "error" not in s["result"]
        for s in result["tool_trace"]
    )
    if grounded and account_id is not None and match_id is not None:
        chat_session.save(account_id, match_id, result["messages"])
    return {
        "review": result["text"],
        "tool_trace": result["tool_trace"],
        "messages": result["messages"],
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
it returns. player_items is what the player
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
