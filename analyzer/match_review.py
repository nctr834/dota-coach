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

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
AGENT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_REVIEW = """You are a Dota 2 post-game coach for a position-1 (carry) player. Review one match and give short, concrete feedback.

The review must read consistently with the result (won from get_match_detail): a loss should never read like a win and if a throw happened, mention it without attributing blame directly. State the result and what it came down to once, in the "Result:" line below, and nowhere else.

Investigate, do not dump. Always start with get_match_detail and compute_metrics: that gives the result, KDA, and the farm/damage/last-hit percentiles. Read that profile, then call only the deeper tools that the profile points to. You are diagnosing, not filling a form.

- Low farm (low GPM/LH percentile): find out why. Call score_lane_matchup (was the lane lost on paper?) and get_combat_timings (did deaths cause it, or was it a hard lane / passive play?).
- Good farm but low hero-damage percentile: an impact problem. Call get_timing_windows (missed power spikes?) and get_build_gaps (wrong or missing items?).
- Lost despite a strong individual game: call get_draft_advantage to check whether the draft was the story.
- Clean dominant win with no weak percentile: little to investigate; a short confirmation is enough. Do not pull every tool to manufacture a critique.
Call a tool when a real question needs it, not by default. It is fine to call one deeper tool, several, or none beyond the baseline.

Your value is judgment, not stat-reading. Reason across whatever you gathered: decide what actually decided this game, connect the dimensions (a missed timing that led to the deaths that lost the lead; elite farm that never converted to damage), and tell the player the one or two things that matter. A coach who lists every stat is useless; one who says "your farm was fine, the game turned on X" is not.

Faithfulness is absolute and separate from judgment. Every number you state must come from a tool result, used as given: never compute, round differently, or invent a figure, a kill/death minute, or a stat no tool reported. A positive advantage is favorable, negative unfavorable; keep the sign. Reasoning and opinion on the real numbers is encouraged; inventing numbers is not.

Structure. Always write "Result:" and "Read:". Add a reference block only for a tool you actually called.
- "Result:" won or lost (from get_match_detail), and what it came down to. On a loss never read like a win; a hard lane or losing draft that beat the player is the result, not the player's failure.
- "Read:" your coaching analysis, a few sentences. Synthesize what you investigated; prioritize, do not enumerate. If they played well and lost to the draft, say that.
- "Item timings:" only if you called get_timing_windows. If it has "missed" entries, write each one's "note" verbatim; otherwise write its "verdict" line. Do not compose your own timing sentence.
- "Pro build reference:" only if you called get_build_gaps. List the player_skipped items pros build that this player did not (core first). If a distinct_build is present, name it as a separate build option (e.g. "pros also run a caster build: Aghanim's Scepter, Eul's, ..."). State only item names and that pros build them; do NOT explain why any item helps, what it counters, or why it suits this game. You do not have that information and would be guessing. The fact that pros build it is the whole point.

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


def review_match(
    account_id: int | None = None,
    match_id: int | None = None,
    max_turns: int = 8,
) -> dict:
    if account_id is None and match_id is None:
        raise ValueError("provide account_id or match_id")
    if match_id is not None:
        ask = f"Review match_id {match_id}" + (
            f" for account_id {account_id}." if account_id else "."
        )
    else:
        ask = f"Review the most recent notable match for account_id {account_id}."

    result = run_agent(SYSTEM_REVIEW, ask, max_turns=max_turns)
    # Persist the review as the opening of the chat session so a follow-up
    # continues this conversation instead of regenerating the review.
    if account_id is not None and match_id is not None:
        chat_session.save(account_id, match_id, result["messages"])
    return {
        "review": result["text"],
        "tool_trace": result["tool_trace"],
        "messages": result["messages"],
    }


SYSTEM_CHAT = """You are a Dota 2 coach continuing a conversation about a match you
already reviewed (the review and its tool results are in the history above).
Answer the player's follow-up directly and concisely, in plain prose, not the
structured review format. Reuse facts already gathered; call a tool only for a
fact you do not yet have. Faithfulness is absolute: every number must come from a
tool result, used as given; never invent or recompute a figure. For pro builds
(get_build_gaps), state which items pros build and which the player skipped, and
name a distinct_build if present; do NOT explain why an item helps or what it
counters, you do not have that and would be guessing. No emojis, no bold."""


def chat_about_match(account_id: int, match_id: int, message: str) -> dict:
    """Answer a follow-up question about a match, continuing the same agent
    conversation. Starts the session with a full review if none exists yet, then
    runs one turn on the question. Persists the updated history."""
    history = chat_session.load(account_id, match_id)
    if history is None:
        review = review_match(account_id=account_id, match_id=match_id)
        history = review["messages"]
    result = run_agent(SYSTEM_CHAT, message, history=history)
    chat_session.save(account_id, match_id, result["messages"])
    return {"reply": result["text"], "tool_trace": result["tool_trace"]}
