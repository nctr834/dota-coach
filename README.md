# Dota Coach

## What it is

Dota Coach reviews a finished Dota 2 match for a position-1 (carry) player and
evaluates drafts. The review is written by a Claude agent that calls tools over
OpenDota and Stratz match data and is instructed to report only what those tools
return; the player can then ask follow-up questions in the same conversation. The
draft evaluator is arithmetic over hero win-rate statistics and does not use a
language model.

## How it works

### Data sources

- **Stratz GraphQL API** (needs `STRATZ_API_KEY`). The data pipeline pulls
  hero-versus-hero and hero-with-hero matchup counts and per-position win rates,
  both for the Divine/Immortal bracket. At review time the tools also query Stratz
  for per-match deep-parse stats (tower damage per minute, farm gold by location),
  item constants, game versions, and parsed games of the six pro carry accounts
  hard-coded in `CARRY_SEED` (`analyzer/tools.py`), which supply the pro item
  builds.
- **d2vpkr**, Valve's game files on GitHub. The pipeline reads hero and ability
  definitions, `items.txt`, localization files, and patch notes from 7.41 onward.
- **OpenDota API** (no key). The review tools fetch match details and
  replay-parsed data, per-hero benchmark percentiles, and a player's recent
  matches. The pipeline also fetches per-hero item popularity and ability
  constants from it.

`scripts/gather_data.py` writes the pipeline's output as JSON in `data/`
(gitignored) and copies `hero_data.json` into `frontend/src/data/` for the UI.
Two optional scripts, `scripts/build_item_tags.py` and
`scripts/build_hero_tags.py`, have Claude Haiku rate each notable item and each
hero on a 0-2 tag vector from Valve's text. Their output is per-patch data
meant to be reviewed by hand. The item tags appear in `get_build_gaps` output as
descriptive labels; nothing at runtime reads the hero tags yet.

### Review agent

`review_match` in `analyzer/match_review.py`, served at `POST /api/review-match`,
takes an account id, a match id, or both.

1. **Parse gate.** With a match id, `ensure_parsed` (`analyzer/utils.py`) checks
   whether OpenDota has replay-parsed data for the match. If it does not, it asks
   OpenDota to parse the replay and returns a fixed message telling the player to
   retry, without calling the model.
2. **Tool loop.** `run_agent` sends the system prompt and the request to Claude
   (`AGENT_MODEL = "claude-sonnet-4-6"`, temperature 0) with the 13 tools
   registered in `TOOLS` (`analyzer/tools.py`). When the model asks for tools, the
   loop runs them and returns the results as JSON; an exception becomes
   `{"error": ...}`. The loop ends when the model answers without a tool call, or
   after 8 turns. Every call and its result go into a tool trace that is returned
   with the review.
3. **Output.** The prompt asks for a `Result:` line and a `Read:` paragraph, plus
   `Item timings:` only if `get_timing_windows` was called and
   `Pro build reference:` only if `get_build_gaps` was called. Code trims anything
   before `Result:` and strips bold and italic markers.

With only an account id, the agent is asked to review "the most recent notable
match", and the `get_recent_matches` description tells it to call that tool first
to pick one.

The review prompt (`SYSTEM_REVIEW`) tells the agent to begin with
`get_match_detail` and `compute_metrics` and to call deeper tools only where that
profile points: for example `get_farm_pattern` on a low farm percentile or any
loss, and `get_draft_advantage` on a loss despite a strong individual game. The
tools return summarized facts rather than raw per-minute arrays:

- `get_recent_matches`: the player's recent matches with hero, result, KDA, GPM
  and duration.
- `get_match_detail`: hero, lane role, KDA, GPM, XPM, side, allied and enemy
  heroes, the team's largest gold lead and deficit with their minutes, and final
  net-worth rank.
- `compute_metrics`: GPM, XPM, last hits per minute, hero damage per minute and
  tower damage with OpenDota benchmark percentiles; anything under the 35th
  percentile is listed in `weak_areas`.
- `get_hero_benchmarks`: the hero's full percentile table.
- `get_combat_timings`: kills and deaths by phase (up to 10 minutes, 10-25, after
  25) and by opposing hero; for each death a context label (`caught_alone`,
  `skirmish`, `died_in_teamfight`, `first_death_of_teamfight`), the player's
  net-worth rank and the team gold advantage at that minute, and whether they
  bought back; `gold_swings`, the turning points where the team gold advantage
  reversed by 2000 or more; buildings the player last-hit; tower damage by phase
  (Stratz); Roshan, tormentor and aegis events.
- `get_timing_windows`: for each "fight item" the player completed, what the next
  3 minutes held. Fight items are learned per hero from the seeded pro games: up
  to five items, each bought in at least 3 of those games, ranked by how often the
  pro got a kill within 3 minutes of completing it. A window is flagged
  `missed_window` when the team was not more than 15,000 gold behind, the player
  got no kills and at least 10 last hits, and the team had a teamfight in which
  the player dealt no damage. The tool writes the note for each missed window
  itself.
- `get_farm_pattern`: last hits at 10, 15, 20, 25, 30 and 40 minutes against
  fixed bands in `_CS_TARGETS`; droughts of 3 or more consecutive minutes with at
  most one last hit per minute, labeled with deaths, teamfights and gold state;
  camps stacked; farm gold by zone (Stratz).
- `score_lane_matchup`: the heroes who shared the player's lane, scored against
  each other with the draft math below, plus each lane hero's last hits and
  denies at 10 minutes and lane efficiency.
- `get_draft_advantage`: both full drafts scored with the draft math.
- `get_build_gaps`: completed-item builds from the seeded pro accounts on the
  same hero in the current major patch (a sample of up to 18 builds against any
  enemy, plus up to 6 against each enemy in the game), reduced to a core (items
  in at least half the builds), alternatives, and a distinct build if one shares
  at most one item with the core. It then lists the player's items with how many
  pro builds contained each, the items from the matching build the player
  skipped, and completion minutes against the pro median.
- `get_break_dispel_targets`: each enemy's passive and basic-dispellable
  abilities from OpenDota's ability constants, the dispellable items they
  carried, and how many sampled pro builds had Silver Edge or Nullifier.
- `get_metric_trend`: the player's average, minimum and maximum of one metric
  over their recent games on a hero.
- `get_patch_notes`: a hero's base-stat and ability changes by patch.

### Follow-up chat

When a review run gets match data (`get_match_detail` succeeded) and both ids are
known, its full message history, tool calls and results included, is saved to
`data/sessions/<account_id>-<match_id>.json`. `POST /api/chat` loads that history
and runs one more agent turn under `SYSTEM_CHAT`, which tells the model to answer
in plain prose, reuse the facts already gathered, and call a tool only for one it
lacks. If no session exists yet, the chat runs a review first.
`GET /api/chat-history` returns the saved conversation for the UI, and
`GET /api/review-history` lists an account's reviewed matches.

`POST /api/query` (`analyzer/process_query.py`) runs the same tool loop for a
question asked on the draft screen. The message carries the current picks, side
and position, and its prompt (`SYSTEM_QUERY`) points the model at
`get_patch_notes` and `get_hero_benchmarks`.

### Draft scoring

`analyzer/counters.py` and `analyzer/evaluator.py` score heroes from two Stratz
tables: `matchup_data` (for each hero pair, wins and games against and with each
other) and `pos_data` (for each position 1-5, each hero's wins and games). Below,
`base(h, p)` is hero `h`'s win rate at position `p`.

Counter score of hero `h` at position `p` against enemy `e` at position `q`, from
their head-to-head wins `W` and games `M`:

```
vs_wr    = (W/5 + 100 * 0.5) / (M/5 + 100)
expected = 0.5 + (base(h, p) - 0.5) - (base(e, q) - 0.5)
counter  = 100 * ((expected + vs_wr) / 2 - 0.5) / 5
```

Synergy score with ally `a` at position `q`, from their wins `W` and games `M`
together:

```
with_wr  = (W/4 + 100 * 0.5) / (M/4 + 100)
expected = 0.5 + (base(h, p) - 0.5) + (base(a, q) - 0.5)
synergy  = 100 * ((expected + with_wr) / 2 - 0.5) / 4
```

The pair win rate is pulled toward 50% by a prior of 100 games, then averaged
with the rate the two heroes' position win rates predict (`expected`). A hero
paired with itself scores 0.

A hero's score in a draft (`evaluate_hero`):

```
score = 100 * (base(h, p) - 0.5)
      + sum over allies  of synergy / 2
      + sum over enemies of counter(h vs e) - counter(e vs h)
```

`rank_picks` (`POST /api/rank-picks`) scores every hero not already picked for
the chosen position and returns them best first. It skips heroes with no data at
that position and heroes that fail a viability check (`_is_viable`): the hero's
`matchCountVs` must be at least 1% of `TOTAL_MATCHES` (the sum of `matchCountVs`
over all heroes, divided by 5), and its games at the chosen position must be at
least its average games per position. `score_teams` (`POST /api/score-teams`)
sums `evaluate_hero` over each team's heroes without the viability check and
returns both totals and their difference.

`GET /api/match-draft-score` and the review tools `score_lane_matchup` and
`get_draft_advantage` apply the same functions to a finished match. Positions are
assigned from each hero's physical lane and last hits (`_team_by_pos`) or, within
a single lane, gold per minute (`_lane_heroes`).

## Grounding

The rule: the model reads verified tool facts and never supplies game knowledge.
In a review it states what the tools returned and does not attribute causes.
`SYSTEM_REVIEW` puts it as "State what a number is, never what it caused." Every
number must come from a tool result as given, not computed, re-rounded or moved
in time. A sentence that attributes an outcome counts as fabrication even when
every number in it is real. Qualities no tool measures (positioning, mechanics,
decision-making, map awareness) are never mentioned, and the model may not claim
one hero counters another from its own knowledge. On a loss, `Read:` ends with
the question the evidence cannot settle rather than a verdict. The chat prompt
repeats the number rule and forbids fault verdicts in either direction. The
offline tag scripts use an LLM only to generate per-patch data that is meant to
be reviewed by hand.

Some of this is enforced in code rather than left to the prompt:

- No parsed replay, no review (the parse gate above).
- A run that never got match data is not saved as a session, so a follow-up
  cannot build on it.
- The `Item timings:` notes are composed by `get_timing_windows`, and the prompt
  requires them verbatim.
- Fields that need the Stratz deep parse come back as `null` when it is missing.

### How the eval checks it

`eval/run_eval.py` runs the review agent on hand-labeled matches from
`eval/labels.json` (gitignored), which holds an account id, a taxonomy of gap
tags, and the true gaps for each match. Only matches with non-empty `true_gaps`
are scored. Reviews are cached in `eval/review_cache.json` and regenerated with
`--fresh`; during the run the session store points at a temporary directory, so
real sessions are not overwritten. Each review is scored three ways:

- **Gap F1.** A judge model (`JUDGE_MODEL = "claude-sonnet-4-6"`, temperature 0)
  sees only the review text and maps it to gap tags; tags outside the taxonomy
  are dropped. Precision, recall and F1 against the labeled gaps are
  computed per match and macro-averaged.
- **Number faithfulness.** A deterministic check: every number in the review,
  after rounding, must be within 1 of some number in that review's tool results,
  including numbers inside string fields such as `gold_swings`. Thousands
  separators are removed and hyphenated ranges split first, and the phase
  boundaries 10 and 25 are always allowed. A review is faithful when it has no
  unsupported numbers.
- **Verdict shapes.** A regex over the review for banned attribution phrasings
  such as "came from", "hinged on", "decided by", "the problem was", "could have
  won" and "positioning was". A review is verdict-free when nothing matches.

The aggregate report gives macro precision, recall and F1, the number of
faithful reviews, the total of unsupported numbers, and the number of
verdict-free reviews.

## Eval results

> **Placeholder, not yet filled in.** Run `python3 eval/run_eval.py` and copy
> the aggregate block here. Nothing below has been measured.

| Field                     | Value  |
| ------------------------- | ------ |
| Commit                    | TODO   |
| Agent model / judge model | TODO   |
| Labeled matches           | TODO   |
| Macro precision           | TODO   |
| Macro recall              | TODO   |
| Macro F1                  | TODO   |
| Faithful reviews          | TODO/N |
| Total unsupported numbers | TODO   |
| Verdict-free reviews      | TODO/N |

## Example review

> **Placeholder.** Paste a real review here, for example the output of
> `python3 scripts/try_review.py <match_id> <account_id>`.

```
TODO: paste review
```

## Limitations

These are the ones visible in the code.

- **Position 1 only.** The review, chat and draft-chat prompts address a
  position-1 (carry) player, the last-hit bands in `get_farm_pattern` are fixed
  position-1 bands, pro builds and fight-timing items come only from the six
  carry accounts in `CARRY_SEED`, and the eval judge tags gaps for a position-1
  player. Nothing changes this for other roles. The draft evaluator itself
  handles any position.
- **Reviews need a parsed match.** Without OpenDota replay data the review
  returns the retry message, which notes that matches older than about two weeks
  may never get parsed data.
- **Stratz dependence.** Fields from the Stratz deep parse are `null` when Stratz
  lacks the deep parse or the key is missing. Without a working key,
  `get_build_gaps`, `get_break_dispel_targets` and, for a hero not yet in the
  cache, `get_timing_windows` raise, and the agent receives an error instead.
- **Player identity.** Every match tool picks the player with
  `_find_player(match, account_id) or match["players"][0]`. With no account id,
  or one not found in the match, the tools silently describe the first player in
  the match. Reviews are saved as sessions only when both ids are given.
- **Inferred positions.** In finished matches, positions come from lane plus last
  hits (or gold per minute within a lane), not from the match data.
  `score_lane_matchup` returns a note instead of a score when the player's lane is
  unknown or jungle, and its score is the heroes' whole-game matchup applied to
  the lane heroes, not a laning-phase measure.
- **Small pro samples.** Builds come from at most 3 parsed games per seeded
  account per query, up to 18 builds for the general sample; the `get_build_gaps`
  description tells the model to state counts, not percentages, for that reason.
  The fight-timing cache (`analyzer/item_timing_cache.json`) is keyed by hero
  only and its Stratz query has no patch filter, so it is not refreshed on a new
  patch unless rebuilt; the matchup-build cache is keyed to the current major
  patch.
- **Fixed data scope.** Matchup and position data cover the Divine/Immortal
  bracket only, patch notes start at 7.41, and both are as current as the last
  `gather_data.py` run.
- **In-process caching.** OpenDota responses are cached in memory for the life of
  the process with no expiry (apart from evicting an unparsed match), so, for
  example, a player's recent-match list does not change until the server
  restarts.
- **Turn cap.** The tool loop stops after 8 turns; a run that hits the cap returns
  the text "max turns reached".
- **Eval coverage.** The faithfulness check matches numbers, not the facts they
  are attached to, and accepts values within 1 of a tool number. The verdict
  regex catches only the phrasings it lists. Gap F1 rests on an LLM judge and on
  one set of hand labels.
- **No access control.** The API has no authentication and allows all CORS
  origins; sessions are plain JSON files under `data/sessions/`.

## Setup and commands

You need Python 3.12, Node.js with npm, and two environment variables in a
`.env` file at the project root. The file is gitignored; never commit it.

- `STRATZ_API_KEY`: used by `gather_data.py` and by the review tools that query
  Stratz.
- `ANTHROPIC_API_KEY`: used by the review, chat and draft-chat agent, the tag
  scripts, and the eval.

Install dependencies:

```bash
pip install -r requirements.txt
cd frontend && npm install
```

Run the data pipeline before anything else: the analyzer loads `data/*.json` at
import, and the frontend imports `frontend/src/data/hero_data.json` at build
time. The two tag scripts are optional; the analyzer treats their output as
empty when it is missing.

```bash
# Data pipeline (per patch)
python3 scripts/gather_data.py         # Refresh all data from APIs
python3 scripts/build_item_tags.py     # Regenerate item tags (LLM, costs tokens)
python3 scripts/build_hero_tags.py     # Regenerate hero tags (LLM, costs tokens)

# Backend API on :8000 (run from project root)
uvicorn api.main:app --reload

# Frontend (Vite proxies /api to the backend)
cd frontend && npm run dev             # Start dev server (Vite)
cd frontend && npm run build           # Production build
cd frontend && npm run lint            # ESLint

# Smoke test (25 checks: imports, data, draft math, OpenDota tools, API routes;
# needs data/ and network, no LLM calls)
python3 scripts/smoke_test.py

# Eval (needs eval/labels.json; LLM calls per labeled match, costs money;
# --fresh regenerates the cached reviews)
python3 eval/run_eval.py
```

Python is formatted with Black through the pre-commit hook in
`.pre-commit-config.yaml`; the frontend is linted with ESLint.

`docs/PROJECT_STORY.md` records how the project got here. Parts of it predate the
current code (it describes a Haiku agent with six tools); where they differ, the
code is current.
