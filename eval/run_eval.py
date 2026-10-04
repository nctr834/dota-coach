"""Evaluate the match-review agent against hand-labeled pos-1 games.

Scores two things per match:
  - gap detection: does the review flag the gap(s) you labeled? (precision/recall/F1)
  - faithfulness: every number in the review must appear in a tool result.

A judge model maps the free-text review to taxonomy tags; faithfulness is a
deterministic code check (review numbers vs tool-result numbers). Labels are yours.

  python3 eval/run_eval.py            # all labeled matches
  python3 eval/run_eval.py -v         # also print each review + judge reasoning
  python3 eval/run_eval.py --read facts   # fact-selection mode (own cache entries)
  python3 eval/run_eval.py --matches 9028258550,9026632686   # only these matches
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
from match_review import review_match, AGENT_MODEL, _by_tool, _fact_sheet

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


def cached_review(match_id: int, fresh: bool = False, read_mode: str = "prose") -> dict:
    """Reviews are stochastic; cache them so judge/scoring changes re-run without
    regenerating the agent. Pass fresh=True (or --fresh) after changing the agent.
    Each read mode has its own cache entry, so both can be compared on the same
    matches."""
    cache = json.loads(REVIEW_CACHE.read_text()) if REVIEW_CACHE.exists() else {}
    key = str(match_id) if read_mode == "prose" else f"{match_id}:{read_mode}"
    if not fresh and key in cache:
        return cache[key]
    result = review_match(
        account_id=LABELS["account_id"], match_id=match_id, read_mode=read_mode
    )
    # The result's "messages" carry SDK objects and are chat-session state, not
    # eval material; cache only what gets scored.
    cache[key] = {
        "review": result["review"],
        "tool_trace": result["tool_trace"],
        "usage": result.get("usage"),
        "selected_facts": result.get("selected_facts"),
    }
    REVIEW_CACHE.write_text(json.dumps(cache, indent=2))
    return cache[key]


JUDGE_SYSTEM = f"""You map a Dota 2 post-game review to gap tags for a position-1
(carry) player. You see only the review text.

Return ONLY a JSON object: {{"predicted_gaps": [<subset of {TAXONOMY}>],
"verdict_phrases": [<exact quotes>]}}. verdict_phrases are short exact quotes
(a few words each) where the review assigns a cause or grades the play. Read
only the "Read:" section for these; the other sections are printed by code.
Verdicts: "the hinge of the game", "catastrophic fights", "the lane was
clean", "tells the story", "driven by the 30m fight", "gave it back through",
"sit right at the inflection", "never stabilized", "clawed back".
Not verdicts: "produced no buildings", "Manta Style unused while you died",
"the lead peaked at +6673 at 27m, then -4384 by 37m", "you died at 31m as the
second of four allies", the closing open question. [] if there are none.

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
            # "50-60" in a tool string is a range, not 50 and -60
            node = re.sub(r"(?<=\d)-(?=\d)", " ", node)
            for tok in re.findall(r"-?\d+(?:\.\d+)?", node):
                _add(float(tok))

    for s in tool_trace:
        walk(s.get("result"))
    return nums


def check_numbers(review: str, tool_trace: list) -> tuple[list[str], list[str]]:
    """(unsupported, sign_flipped) numbers in the review. A value is supported
    if its rounded int is within 1 of a tool value (covers rounded floats); the
    phase boundary minutes the agent uses to name buckets are always allowed.
    sign_flipped are values only the negation of which a tool returned: a
    tool's -4109 restated as "4109 behind" is the same fact, but a direction
    error would look the same, so they are counted apart."""
    facts = _fact_numbers(tool_trace) | PHASE_BOUNDARIES
    review = re.sub(r"(?<=\d),(?=\d)", "", review)
    # "22-35 minutes" is a range and "tier-4" a hyphenation, not negative numbers.
    review = re.sub(r"(?<=[\dA-Za-z])-(?=\d)", " ", review)
    bad, flipped = [], []
    for token in re.findall(r"-?\d+(?:\.\d+)?", review):
        val = round(float(token))
        if val in facts:
            continue
        (flipped if -val in facts else bad).append(token)
    return bad, flipped


def unsupported_numbers(review: str, tool_trace: list) -> list[str]:
    return check_numbers(review, tool_trace)[0]


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


def read_section(review: str) -> str:
    read = re.search(r"Read:(.*?)(?=\n\s*\n[A-Z][\w' ]*:|\Z)", review, re.DOTALL)
    return read.group(1) if read else ""


def review_length(review: str) -> tuple[int, int]:
    """(total words, sentences in the "Read:" section)."""
    sentences = re.findall(r"[.?!](?:\s|$)", read_section(review))
    return len(review.split()), len(sentences)


def uncited(text: str, must_cite: list[str]) -> list[str]:
    """Labeled must_cite strings (case-insensitive) the text does not contain."""
    text = text.lower()
    return [fact for fact in must_cite if fact.lower() not in text]


def score_gaps(true_gaps: set, predicted: set):
    tp = len(true_gaps & predicted)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(true_gaps) if true_gaps else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def main(argv):
    verbose = "-v" in argv
    read_mode = argv[argv.index("--read") + 1] if "--read" in argv else "prose"
    labeled = [m for m in LABELS["matches"] if m["true_gaps"]]
    if "--matches" in argv:
        only = set(argv[argv.index("--matches") + 1].split(","))
        labeled = [m for m in labeled if str(m["match_id"]) in only]
    if not labeled:
        print("No labeled matches. Fill in 'true_gaps' in eval/labels.json.")
        return 1

    fresh = "--fresh" in argv
    print(
        f"agent={AGENT_MODEL}  judge={JUDGE_MODEL}  read={read_mode}  "
        f"matches={len(labeled)}\n"
    )
    rows = []
    for m in labeled:
        result = cached_review(m["match_id"], fresh=fresh, read_mode=read_mode)
        review = result["review"]
        selected = result.get("selected_facts")
        j = predict_gaps(review)
        true_g = set(m["true_gaps"])
        pred_g = set(j.get("predicted_gaps", [])) & TAXONOMY_SET
        claims, flipped = check_numbers(review, result["tool_trace"])
        verdicts = verdict_shapes(review)
        words, read_sentences = review_length(review)
        must_cite = m.get("must_cite") or []
        # Only what the model chose counts: the Read it wrote, or the fact lines
        # it selected. Blocks code prints every time would pass trivially.
        chosen = (
            " ".join(f["line"] for f in selected)
            if selected is not None
            else read_section(review)
        )
        missing = uncited(chosen, must_cite)
        # Stricter than the trace check: numbers the model chose to state that
        # are not on the fact sheet or the Result line.
        by_tool = _by_tool(result["tool_trace"])
        sheet = [f["line"] for f in _fact_sheet(by_tool)]
        sheet.append((by_tool.get("get_match_detail") or {}).get("result_line") or "")
        off_sheet = check_numbers(chosen, [{"result": sheet}])[0]
        usage = result.get("usage") or {}
        row = {
            "judge": score_gaps(true_g, pred_g),
            "unsupported": len(claims),
            "verdicts": len(verdicts),
            "flipped": len(flipped),
            "words": words,
            "numbers": len(re.findall(r"\d+(?:\.\d+)?", review)),
            "off_sheet": len(off_sheet),
            "read_sentences": read_sentences,
            "must_cite": len(must_cite),
            "missing": len(missing),
            "tokens_in": usage.get("input_tokens"),
            "tokens_out": usage.get("output_tokens"),
        }

        print(f"match {m['match_id']}")
        print(f"  true:      {sorted(true_g)}")
        print(f"  predicted: {sorted(pred_g)}")
        flag = "  [JUDGE PARSE ERROR]" if j.get("_parse_error") else ""
        p, r, f1 = row["judge"]
        print(f"  judge P={p:.2f} R={r:.2f} F1={f1:.2f}{flag}")
        if selected is not None:
            cats = {f["category"] for f in selected if f["category"]} or {"no_gap"}
            row["category"] = score_gaps(true_g, cats)
            p, r, f1 = row["category"]
            print(f"  selected:  {[f['id'] for f in selected]} -> {sorted(cats)}")
            print(f"  category P={p:.2f} R={r:.2f} F1={f1:.2f}")
        print(
            f"  length: {words} words, {read_sentences} Read sentences, "
            f"{row['numbers']} numbers"
        )
        if off_sheet:
            print(
                f"  numbers not on the fact sheet ({len(off_sheet)}): {', '.join(off_sheet)}"
            )
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
        if flipped:
            print(f"  sign-flipped numbers ({len(flipped)}): {', '.join(flipped)}")
        if j.get("verdict_phrases"):
            # a reading aid for the reviewer, not scored
            print(f"  judge-flagged phrases: {'; '.join(j['verdict_phrases'])}")
        if verbose:
            print(f"  --- review ---\n{review}\n")
        print()
        rows.append(row)

    n = len(rows)

    def mean(values) -> float:
        values = list(values)
        return sum(values) / len(values)

    print("=== aggregate ===")
    for i, name in enumerate(("precision", "recall", "F1")):
        print(f"  judge macro {name}: {mean(x['judge'][i] for x in rows):.2f}")
    scored = [x["category"] for x in rows if "category" in x]
    if scored:
        # tags come from the selected facts' categories, not from the judge, so
        # this is a different metric from the judge F1 above
        for i, name in enumerate(("precision", "recall", "F1")):
            print(f"  category macro {name}: {mean(x[i] for x in scored):.2f}")
    print(f"  faithful reviews: {sum(1 for x in rows if not x['unsupported'])}/{n}")
    print(f"  total unsupported numbers: {sum(x['unsupported'] for x in rows)}")
    print(f"  regex-clean reviews: {sum(1 for x in rows if not x['verdicts'])}/{n}")
    print(f"  sign-flipped numbers: {sum(x['flipped'] for x in rows)}")
    print(f"  numbers not on the fact sheet: {sum(x['off_sheet'] for x in rows)}")
    print(f"  mean words: {mean(x['words'] for x in rows):.0f}")
    print(f"  mean numbers cited: {mean(x['numbers'] for x in rows):.0f}")
    print(f"  mean Read sentences: {mean(x['read_sentences'] for x in rows):.1f}")
    total_cite = sum(x["must_cite"] for x in rows)
    if total_cite:
        cited = total_cite - sum(x["missing"] for x in rows)
        print(f"  must-cite facts cited: {cited}/{total_cite}")
    metered = [x for x in rows if x["tokens_in"] is not None]
    if metered:
        print(
            f"  mean tokens ({len(metered)} reviews with usage): "
            f"{mean(x['tokens_in'] for x in metered):.0f} in, "
            f"{mean(x['tokens_out'] for x in metered):.0f} out"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
