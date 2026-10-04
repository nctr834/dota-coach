"""Evaluate the match-review agent against hand-labeled pos-1 games.

Scores two things per match:
  - gap detection: does the review flag the gap(s) you labeled? (precision/recall/F1)
  - faithfulness: every number in the review must appear in a tool result.

A judge model maps the free-text review to taxonomy tags; faithfulness is a
deterministic code check (review numbers vs tool-result numbers). Labels are yours.

  python3 eval/run_eval.py            # all labeled matches
  python3 eval/run_eval.py -v         # also print each review + judge reasoning
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analyzer"))
os.chdir(ROOT)

import chat_session
from match_review import review_match, AGENT_MODEL

# Eval reviews must not overwrite the user's real saved sessions for these
# matches, so the session store points at a throwaway dir for this process.
chat_session._DIR = Path(tempfile.mkdtemp(prefix="eval-sessions-"))

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
JUDGE_MODEL = "claude-sonnet-4-6"

LABELS = json.loads((ROOT / "eval" / "labels.json").read_text())
TAXONOMY = LABELS["taxonomy"]
TAXONOMY_SET = set(TAXONOMY)

REVIEW_CACHE = ROOT / "eval" / "review_cache.json"


def cached_review(match_id: int, fresh: bool = False) -> dict:
    """Reviews are stochastic; cache them so judge/scoring changes re-run without
    regenerating the agent. Pass fresh=True (or --fresh) after changing the agent."""
    cache = json.loads(REVIEW_CACHE.read_text()) if REVIEW_CACHE.exists() else {}
    key = str(match_id)
    if not fresh and key in cache:
        return cache[key]
    result = review_match(account_id=LABELS["account_id"], match_id=match_id)
    # The result's "messages" carry SDK objects and are chat-session state, not
    # eval material; cache only what gets scored.
    cache[key] = {
        "review": result["review"],
        "tool_trace": result["tool_trace"],
        "usage": result.get("usage"),
    }
    REVIEW_CACHE.write_text(json.dumps(cache, indent=2))
    return cache[key]


JUDGE_SYSTEM = f"""You map a Dota 2 post-game review to gap tags for a position-1
(carry) player. You see only the review text.

Return ONLY a JSON object: {{"predicted_gaps": [<subset of {TAXONOMY}>],
"verdict_phrases": [<exact quotes>]}}. verdict_phrases are short exact quotes
(a few words each) where the review assigns a cause or grades the play:
causal connectors ("driven by", "because of", "cost them", "erased it", "gave
it back through", "tells the story") and grading words ("catastrophic",
"clean lane", "the hinge of the game", "played well", "wipeout"). A statement
of what happened with its numbers and minutes is not a verdict, whatever it
describes ("the lead went from +6673 to -4384", "no buildings taken", "died at
31m"), and neither is the closing open question. Ignore the lines under
"Objectives:", "Item timings:" and "Pro build reference:". [] if there are
none.
predicted_gaps must be a subset of exactly these tags: {TAXONOMY}. Never output
any string outside this list (not a metric name, not a hero name).

The review renders no verdicts; it lays out evidence and ends on an open
question. Tag the candidate reasons its "Read:" foregrounds as evidence:
deaths cited with context (caught alone, first in fight, farmed and behind)
-> deaths; a lost lane or missed CS checkpoints -> lane_cs; farm droughts or
slow farm after laning -> mid_game_farm; item choices or timings questioned
-> itemization; power spikes farmed through, fights the team took without the
player, low fight participation -> teamfight_impact; an aegis, a won fight or
a gold lead followed by no buildings or objectives, or a tormentor left up
while ahead -> objective_conversion; an owned item not used in a fight ->
item_usage. A hard lane or losing
draft mentioned as context is not a gap; tag lane_matchup_disadvantage or
draft_disadvantage only when the Read centers it as the leading candidate.
A clean confirmation flagging nothing -> ["no_gap"]."""


def predict_gaps(review: str) -> dict:
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=600,
        temperature=0,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": f"REVIEW:\n{review}"}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    start = text.find("{")
    if start == -1:
        return {"predicted_gaps": [], "_parse_error": True}
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except json.JSONDecodeError:
        return {"predicted_gaps": [], "_parse_error": True}


# Phase-boundary minutes the agent names to describe the buckets get_combat_timings
# returns (laning <=10, mid 10-25, late >25), not stats it could fabricate.
PHASE_BOUNDARIES = {10, 25}


def _fact_numbers(tool_trace: list) -> set[int]:
    """Every numeric tool value, as a set of rounded ints and their +/-1
    neighbors, so a review that rounds a long float still matches. Numbers
    embedded in string facts count too — gold_swings, largest_team_deficit,
    building/objective timelines, and CS targets are strings by design."""
    nums: set[int] = set()

    def _add(x: float):
        r = round(x)
        nums.update((r - 1, r, r + 1))

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            _add(node)
        elif isinstance(node, str):
            for tok in re.findall(r"-?\d+(?:\.\d+)?", node):
                _add(float(tok))

    for s in tool_trace:
        walk(s.get("result"))
    return nums


def unsupported_numbers(review: str, tool_trace: list) -> list[str]:
    """Numbers in the review that no tool returned. A value matches if its
    rounded int is within 1 of a tool value (covers rounded floats); the phase
    boundary minutes the agent uses to name buckets are always allowed."""
    facts = _fact_numbers(tool_trace)
    review = re.sub(r"(?<=\d),(?=\d)", "", review)
    # "22-35 minutes" is a range and "tier-4" a hyphenation, not negative numbers.
    review = re.sub(r"(?<=[\dA-Za-z])-(?=\d)", " ", review)
    bad = []
    for token in re.findall(r"-?\d+(?:\.\d+)?", review):
        val = round(float(token))
        # a tool's -4109 restated as "4109 behind" is the same fact
        if {val, -val} & (facts | PHASE_BOUNDARIES):
            continue
        bad.append(token)
    return bad


_VERDICT_SHAPES = re.compile(
    r"\b(came from|came through|hinged on|decided by|closed (it|the game) out"
    r"|turned the (deficit|game|tide)|the (problem|issue|reason) (was|is)"
    r"|was(n't| not) the (problem|issue)|could( not|n't)? have won"
    r"|(execution|mechanics|positioning) (was|were))\b",
    re.IGNORECASE,
)


def verdict_shapes(review: str) -> list[str]:
    """Attribution phrasings the prompt bans — cause claims the data cannot
    settle. Deterministic regex, so prompt regressions surface without
    hand-reading reviews."""
    return [m.group(0) for m in _VERDICT_SHAPES.finditer(review)]


def review_length(review: str) -> tuple[int, int]:
    """(total words, sentences in the "Read:" section)."""
    read = re.search(r"Read:(.*?)(?=\n\s*\n[A-Z][\w' ]*:|\Z)", review, re.DOTALL)
    sentences = re.findall(r"[.?!](?:\s|$)", read.group(1)) if read else []
    return len(review.split()), len(sentences)


def uncited(review: str, must_cite: list[str]) -> list[str]:
    """Labeled must_cite strings (case-insensitive) the review does not contain."""
    text = review.lower()
    return [fact for fact in must_cite if fact.lower() not in text]


def score_gaps(true_gaps: set, predicted: set):
    tp = len(true_gaps & predicted)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(true_gaps) if true_gaps else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def main(argv):
    verbose = "-v" in argv
    labeled = [m for m in LABELS["matches"] if m["true_gaps"]]
    if not labeled:
        print("No labeled matches. Fill in 'true_gaps' in eval/labels.json.")
        return 1

    fresh = "--fresh" in argv
    print(f"agent={AGENT_MODEL}  judge={JUDGE_MODEL}  matches={len(labeled)}\n")
    rows = []
    total_unsupported = 0
    for m in labeled:
        result = cached_review(m["match_id"], fresh=fresh)
        j = predict_gaps(result["review"])
        true_g = set(m["true_gaps"])
        pred_g = set(j.get("predicted_gaps", [])) & TAXONOMY_SET
        p, r, f1 = score_gaps(true_g, pred_g)
        claims = unsupported_numbers(result["review"], result["tool_trace"])
        total_unsupported += len(claims)
        verdicts = verdict_shapes(result["review"])
        words, read_sentences = review_length(result["review"])
        must_cite = m.get("must_cite") or []
        missing = uncited(result["review"], must_cite)
        usage = result.get("usage") or {}
        rows.append(
            (
                m["match_id"],
                true_g,
                pred_g,
                p,
                r,
                f1,
                len(claims),
                len(verdicts),
                words,
                read_sentences,
                len(must_cite),
                len(missing),
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                len(j.get("verdict_phrases") or []),
            )
        )

        print(f"match {m['match_id']}")
        print(f"  true:      {sorted(true_g)}")
        print(f"  predicted: {sorted(pred_g)}")
        flag = "  [JUDGE PARSE ERROR]" if j.get("_parse_error") else ""
        print(f"  P={p:.2f} R={r:.2f} F1={f1:.2f}{flag}")
        print(f"  length: {words} words, {read_sentences} Read sentences")
        if must_cite:
            print(f"  must-cite: {len(must_cite) - len(missing)}/{len(must_cite)}")
            if missing:
                print(f"  not cited: {', '.join(missing)}")
        if usage:
            print(f"  tokens: {usage['input_tokens']} in, {usage['output_tokens']} out")
        if claims:
            print(f"  unsupported numbers ({len(claims)}): {', '.join(claims)}")
        if verdicts:
            print(f"  verdict shapes ({len(verdicts)}): {', '.join(verdicts)}")
        if j.get("verdict_phrases"):
            print(f"  judge verdict phrases: {'; '.join(j['verdict_phrases'])}")
        if verbose:
            print(f"  --- review ---\n{result['review']}\n")
        print()

    n = len(rows)
    print("=== aggregate ===")
    print(f"  macro precision: {sum(x[3] for x in rows)/n:.2f}")
    print(f"  macro recall:    {sum(x[4] for x in rows)/n:.2f}")
    print(f"  macro F1:        {sum(x[5] for x in rows)/n:.2f}")
    print(f"  faithful reviews: {sum(1 for x in rows if x[6]==0)}/{n}")
    print(f"  total unsupported numbers: {total_unsupported}")
    print(f"  regex-clean reviews: {sum(1 for x in rows if x[7]==0)}/{n}")
    print(f"  judge verdict-free reviews: {sum(1 for x in rows if x[14]==0)}/{n}")
    print(f"  mean words: {sum(x[8] for x in rows)/n:.0f}")
    print(f"  mean Read sentences: {sum(x[9] for x in rows)/n:.1f}")
    total_cite = sum(x[10] for x in rows)
    if total_cite:
        print(
            f"  must-cite facts cited: {total_cite - sum(x[11] for x in rows)}/{total_cite}"
        )
    metered = [x for x in rows if x[12] is not None]
    if metered:
        k = len(metered)
        print(
            f"  mean tokens ({k} reviews with usage): "
            f"{sum(x[12] for x in metered)/k:.0f} in, {sum(x[13] for x in metered)/k:.0f} out"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
