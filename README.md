# Dota Coach

A post-game coach for position-1 (carry) Dota 2 players, plus a draft evaluator.
You give it a match and your account id; it gives back a short review of that
match, and you can ask follow-up questions in the same conversation.

Language models give confident, wrong Dota advice. Two ideas keep that out of the
review.

## 1. Code owns the facts; the model only picks or phrases them

The review is not a model reading a match. `review_match`
(`analyzer/match_review.py`) runs a fixed set of tools in code over OpenDota and
Stratz data: match profile and benchmark percentiles, deaths and gold swings,
every teamfight, aegis and tormentor windows, farm, lane and draft scores, and,
on a loss or a game with weak hero damage but not weak farm, item timings and pro
builds. The tools return summarized facts, and several return ready-made lines.

The model gets those results and does one narrow job:

- **Prose mode** (default): it writes the Read, a paragraph of at most four
  sentences. Code prints everything else verbatim from the tool results: the
  Result line, notable deaths, lost fights the player dealt no damage in,
  objectives, item timings and the pro build reference.
- **Fact mode** (`REVIEW_READ_MODE=facts`): code builds a numbered fact sheet and
  the model returns up to four fact ids. It writes no text at all. Code prints
  the chosen lines, plus a few kinds of deaths and fights it always prints.

The prompt's rule is "State what a number is, never what it caused." Every
number must come from a tool result as given. Attributing an outcome to a cause
counts as fabrication even when every number is real. Qualities no tool
measures (positioning, mechanics, decision-making) are never mentioned. The
player draws the conclusions.

Code also refuses to review when it cannot be sure of its facts:

- **The match must be parsed.** If OpenDota has no parsed replay, the review
  requests a parse and asks the player to retry. It does not call the model.
- **The account must be in the match.** If the account id is missing, or is not
  among the match's players, the review says so. It never falls back to someone
  else's game.

Follow-up questions (`POST /api/chat`) continue the saved conversation. They
reuse the facts already gathered and call further tools only for a fact the
conversation does not have yet.

## 2. The eval checks faithfulness without trusting the model

`eval/run_eval.py` runs the review on hand-labeled matches and scores it.
`eval/labels.json` holds the labeled gaps and must-cite facts for each match.
The faithfulness checks are deterministic code, not a model's opinion:

- **Unsupported numbers.** Every number in the review must match a number in
  that review's tool results. Sign-flipped matches are counted separately.
- **Numbers not on the fact sheet.** A stricter version of the same check,
  applied only to what the model chose to say.
- **Verdict shapes.** A regex for banned attribution phrasings, such as "came
  from", "decided by" or "the problem was".
- **Must-cite facts.** Labeled facts must appear in the part the model chose:
  its Read, or its selected fact lines.

Gap detection is scored too. A judge model maps each review to gap tags, and
precision, recall and F1 are computed against the labels. Fact mode also gets a
category-based score that needs no judge.

### Eval results

> **Placeholder, not yet filled in.** Paste the aggregate block from
> `python3 eval/run_eval.py` (and `--read facts`). Nothing here has been
> measured.

| | Prose mode | Fact mode |
|---|---|---|
| Commit, labeled matches | TODO | TODO |
| Gap F1 (judge / category) | TODO | TODO |
| Faithful reviews | TODO/N | TODO/N |
| Numbers not on the fact sheet | TODO | TODO |
| Regex-clean reviews | TODO/N | TODO/N |
| Must-cite facts cited | TODO | TODO |

### Example review

> **Placeholder.** Paste a real review, for example from
> `python3 scripts/try_review.py <match_id> <account_id>`.

```
TODO: paste review
```

## Draft evaluator

The draft side uses no language model. It is win-rate arithmetic in
`analyzer/counters.py` and `analyzer/evaluator.py`, over Stratz
Divine/Immortal data:

- **Hero score.** A hero's score at a position is its position win rate, plus
  its synergy with each ally, plus its counter edge against each enemy.
- **Pair win rates.** These are pulled toward 50% by a 100-game prior, then
  averaged with what the two heroes' position win rates predict.

There are two endpoints:

- `POST /api/rank-picks` ranks every viable hero for an open position.
- `POST /api/score-teams` scores two drafts against each other.

The review reuses the same math for its lane and draft scores.

## Limitations

These are visible in the code:

- **Position 1 only.** The prompts, the last-hit target bands, the six seeded
  pro carry accounts and the eval judge all assume a carry. The draft evaluator
  handles any position.
- **Small pro samples.** Pro builds and fight-timing items come from at most 3
  parsed current-patch games per seeded account per query.
- **Inferred positions.** Positions in a finished match are inferred from lane
  and last hits, not read from the match.
- **Fixed data scope.** Matchup and position data cover one bracket
  (Divine/Immortal). Patch notes start at 7.41. All of it is only as fresh as
  the last `gather_data.py` run.
- **Stratz dependence.** Without a working Stratz key, deep-parse fields are
  empty and the item tools return errors.
- **Stale cache.** OpenDota responses are cached in memory for the life of the
  server process.
- **What the eval misses.** The number check matches values, not the fact a
  value is attached to. The regex only catches the phrasings it lists. Gap
  scores rest on one person's labels.
- **No access control.** The API has no authentication, and chat sessions are
  plain JSON files in `data/sessions/`.

## Setup

You need Python 3.12, Node.js with npm, and two keys in a `.env` file at the
project root:

- `STRATZ_API_KEY`
- `ANTHROPIC_API_KEY`

`.env` is gitignored; never commit it.

```bash
pip install -r requirements.txt
cd frontend && npm install && cd ..

python3 scripts/gather_data.py         # data/*.json and the UI's hero data (run first, per patch)
python3 scripts/build_item_tags.py     # optional item tags (LLM, costs tokens)

uvicorn api.main:app --reload          # API on :8000, run from project root
cd frontend && npm run dev             # Vite dev server, proxies /api to :8000

python3 scripts/smoke_test.py          # 30 checks; needs data/ and network, no LLM calls
python3 eval/run_eval.py               # needs eval/labels.json; costs money; --fresh, --read facts
```

`CLAUDE.md` covers architecture and conventions for contributors.
`docs/PROJECT_STORY.md` records how the project got here.
