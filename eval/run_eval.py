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
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analyzer"))
os.chdir(ROOT)

from match_review import review_match, AGENT_MODEL

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
    cache[key] = result
    REVIEW_CACHE.write_text(json.dumps(cache, indent=2))
    return result


JUDGE_SYSTEM = f"""You map a Dota 2 post-game review to gap tags for a position-1
(carry) player. You see only the review text.

Return ONLY a JSON object: {{"predicted_gaps": [<subset of {TAXONOMY}>]}}.
predicted_gaps must be a subset of exactly these tags: {TAXONOMY}. Never output
any string outside this list (not a metric name, not a hero name).

Read ONLY the "Main gap:" section; ignore "What went well", "Minor notes", and
nitpicks elsewhere. If it says "none, played well", return ["no_gap"]. Map the
mistake it names to the matching tag(s). A hard lane or losing draft framed as
context the player overcame is not a gap; tag lane_matchup_disadvantage or
draft_disadvantage only if "Main gap" blames it for the loss."""


def predict_gaps(review: str) -> dict:
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=300,
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
    neighbors, so a review that rounds a long float still matches."""
    nums: set[int] = set()

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
            r = round(node)
            nums.update((r - 1, r, r + 1))

    for s in tool_trace:
        walk(s.get("result"))
    return nums


def unsupported_numbers(review: str, tool_trace: list) -> list[str]:
    """Numbers in the review that no tool returned. A value matches if its
    rounded int is within 1 of a tool value (covers rounded floats); the phase
    boundary minutes the agent uses to name buckets are always allowed."""
    facts = _fact_numbers(tool_trace)
    review = re.sub(r"(?<=\d),(?=\d)", "", review)
    bad = []
    for token in re.findall(r"-?\d+(?:\.\d+)?", review):
        val = round(float(token))
        if val in facts or val in PHASE_BOUNDARIES or -val in PHASE_BOUNDARIES:
            continue
        bad.append(token)
    return bad


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
        rows.append((m["match_id"], true_g, pred_g, p, r, f1, len(claims)))

        print(f"match {m['match_id']}")
        print(f"  true:      {sorted(true_g)}")
        print(f"  predicted: {sorted(pred_g)}")
        flag = "  [JUDGE PARSE ERROR]" if j.get("_parse_error") else ""
        print(f"  P={p:.2f} R={r:.2f} F1={f1:.2f}{flag}")
        if claims:
            print(f"  unsupported numbers ({len(claims)}): {', '.join(claims)}")
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
