"""Evaluate the match-review agent against hand-labeled pos-1 games.

Scores two things per match:
  - gap detection: does the review flag the gap(s) you labeled? (precision/recall/F1)
  - faithfulness: is every numeric claim in the review traceable to a tool result?

A judge model maps the free-text review to taxonomy tags and checks faithfulness;
the labels (ground truth) are yours.

  python3 eval/run_eval.py            # all labeled matches
  python3 eval/run_eval.py -v         # also print each review + judge reasoning
"""

import json
import os
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

JUDGE_SYSTEM = f"""You grade a Dota 2 post-game review for a position-1 (carry) player.

You are given the review text and the raw tool results the review was built from.

Return ONLY a JSON object:
{{
  "predicted_gaps": [<subset of {TAXONOMY}>],
  "unsupported_claims": [<each sentence stating a number/fact not present in the tool results>]
}}

predicted_gaps: which of the taxonomy tags the review actually identifies as the
player's main problem(s). Use "no_gap" only if the review says the player played
well with no major mistake. Be strict: only tag a gap the review clearly calls out.

unsupported_claims: list any claim in the review whose specific numbers or facts
do not appear in the tool results (the model inventing data). An empty list means
fully faithful."""


def judge(review: str, tool_trace: list) -> dict:
    results = [{"tool": s["tool"], "result": s.get("result")} for s in tool_trace]
    prompt = (
        f"REVIEW:\n{review}\n\nTOOL RESULTS:\n{json.dumps(results, indent=2)}"
    )
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=1000,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    start, end = text.find("{"), text.rfind("}") + 1
    return json.loads(text[start:end])


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

    print(f"agent={AGENT_MODEL}  judge={JUDGE_MODEL}  matches={len(labeled)}\n")
    rows = []
    total_unsupported = 0
    for m in labeled:
        result = review_match(account_id=LABELS["account_id"], match_id=m["match_id"])
        j = judge(result["review"], result["tool_trace"])
        true_g = set(m["true_gaps"])
        pred_g = set(j.get("predicted_gaps", []))
        p, r, f1 = score_gaps(true_g, pred_g)
        n_unsup = len(j.get("unsupported_claims", []))
        total_unsupported += n_unsup
        rows.append((m["match_id"], true_g, pred_g, p, r, f1, n_unsup))

        print(f"match {m['match_id']}")
        print(f"  true:      {sorted(true_g)}")
        print(f"  predicted: {sorted(pred_g)}")
        print(f"  P={p:.2f} R={r:.2f} F1={f1:.2f}  unsupported_claims={n_unsup}")
        if verbose:
            for c in j.get("unsupported_claims", []):
                print(f"    - UNSUPPORTED: {c}")
            print(f"  --- review ---\n{result['review']}\n")
        print()

    n = len(rows)
    print("=== aggregate ===")
    print(f"  macro precision: {sum(x[3] for x in rows)/n:.2f}")
    print(f"  macro recall:    {sum(x[4] for x in rows)/n:.2f}")
    print(f"  macro F1:        {sum(x[5] for x in rows)/n:.2f}")
    print(f"  faithful reviews: {sum(1 for x in rows if x[6]==0)}/{n}")
    print(f"  total unsupported claims: {total_unsupported}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
