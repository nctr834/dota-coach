# Dota Coach — implementation story

A record of how the project developed, stage by stage, with the reasoning
behind each shift. Dates are from git history.

## Stage 1 — Data foundation (early January 2026)

The project started as a data problem, not a coaching problem. The first commits
pulled hero data from OpenDota and built a script to gather hero and matchup data
(`088a506`, `465eccd`). The generated JSON was committed at this point to show
results while the pipeline stabilised, even though it would later be gitignored.

The data sources settled early: Stratz GraphQL for hero matchups and position win
rates, d2vpkr for Valve game files (abilities, items, patch notes), and OpenDota
for item build popularity.

## Stage 2 — The draft evaluator (January 7)

The first real feature was math, not an LLM. `1c274f5` built a draft evaluator:
given the position you are drafting for, it adds a synergy score for each ally
already picked and subtracts a counter score for each enemy. Heroes are filtered
for positional viability (games-in-position above the hero's average) and a games
threshold to cut noise.

This is the part of the project that has stayed solid throughout. The scoring is
statistical and backed by real win-rate data; it does not depend on a language
model being right about Dota.

## Stage 3 — Structure and the first RAG attempt (late January – February)

`fe9da22` split the evaluator and composition logic into separate classes and
introduced reference hero data plus the first RAG context. The matchup query and
data structures were revised as the shape of the data became clearer (`69c2a5a`,
`e2b65aa`).

The bet at this stage: feed coach knowledge into the system via retrieval. That
led directly to the next stage.

## Stage 4 — Coach transcripts (February 23–25)

A pipeline was built to scrape and condense YouTube transcripts from Dota coaches
(BSJ, PainDota, ZQuixotix): `scrape_transcripts.py`, `condense_transcripts.py`,
`create_chromadbs.py`, and the condensed transcript JSON (`33fde07`, `7423828`,
`e2b65aa`). The idea was to ground gameplay advice in expert commentary rather
than the model's own Dota knowledge.

In parallel, work began on `process_query.py` — the layer that assembles
structured context for the LLM (`3707504`, `f30d10d`).

## Stage 5 — Full stack, and an honest assessment (March 6–7)

`63b997b` was a large step: data structure updates, Aghanim's and patch-note
retrieval, a React frontend, and the frontend wired to the backend. The commit
message also recorded two honest problems: "rag context is horrible rn" and GSI
"isn't linked to anything yet."

`536989a` followed with refactoring and better null checking. This is the current
tip of `main`.

By the end of this stage the project had three things working (draft scoring,
data pipeline, frontend) and two things not paying off (the transcript-based RAG
for gameplay advice, and a GSI integration with no consumer). The recurring
finding: the LLM is a good formatter and a bad Dota expert. It gives confidently
wrong advice, and the transcript RAG was not fixing that.

## Stage 6 — The pivot: cut the dead weight (this session)

The decision was to stop trying to make an LLM reason about live Dota and instead
build something the data can answer directly: post-game analysis.

Two parts of the codebase did not serve that goal and were shelved rather than
deleted:

- The transcript pipeline (scrape / condense / chromadbs and the transcript data)
  was moved to the `feature/transcript-pipeline` branch and removed from the main
  working tree.
- The GSI work-in-progress was moved to the `feature/gsi` branch (commit
  `aab032e`). Live in-game state is a v2 concern; post-game coaching does not need
  it.

The RAG item content was kept, because by this point it had become wired into the
live item-formatting path in `process_query.py` rather than being part of the
abandoned transcript experiment.

## Stage 7 — The agentic match coach (this session)

The new core. The key realisation: OpenDota's `/benchmarks?hero_id=` endpoint
already returns percentile distributions per hero for GPM, XPM, last-hits/min, and
damage. That is a bracket benchmark with no benchmark-building and no ML required.
Combined with `/players/{id}/matches` and parsed `/matches/{id}` data (per-minute
gold/xp/lh arrays, purchase logs, teamfights), it is enough to analyse a game.

Rather than hardcode "fetch then compute then narrate," the implementation gives a
Claude agent six tools and lets it decide what to investigate:

- `get_recent_matches`, `get_match_detail`
- `compute_metrics`, `get_hero_benchmarks`
- `get_death_timings`
- `get_matchup_difficulty` (reuses the Stage 2 counter engine)

The discipline that makes this genuinely agentic: tool calls are contingent on
findings, not a fixed sequence. Verified on a real match — the same game, two
players, produced two different tool paths:

- High-farm carry (90th percentile, 3 deaths): match detail then metrics, then
  stop.
- Low-farm support (20th percentile, 14 deaths): match detail, metrics, then
  branched into death timings.

The next tool depends on the last result. Tools return summarised numbers
(percentiles, minutes) rather than raw per-minute arrays, which keeps token cost
down and keeps the agent reasoning over figures.

One honesty constraint shaped a tool: OpenDota has no clean per-death timeline
(`life_state` is a summary histogram, not a per-second series). Rather than
fabricate death minutes, `get_death_timings` reports the authoritative death
count, seconds spent dead, teamfight-localised death minutes, and an explicit
count of deaths it cannot timestamp.

The agent runs on Haiku for now (cheap iteration on the loop logic), with the
model behind a single constant so the swap to Sonnet is one line. Exposed via
`POST /api/review-match`.

## Where it stands

- Solid: draft evaluator (math-backed), data pipeline, frontend, the new agentic
  match review.
- Shelved on branches: transcript RAG pipeline, GSI.
- v1.1 work not yet built: CS@10 and item-timing benchmarks (these need custom
  computation beyond what OpenDota benchmarks directly).
