# specdeck

Executable behavioral specs for LLM systems. A domain expert writes a prose criterion.
A developer wires deterministic constraints under it. The runner executes the resulting
cards and reports a gate pass rate and a credit score, never blended. The provider ×
prompt matrix, cost, and judge drift are the next three phases.

> **Status: Phase 1 complete.** Five cards run end to end against recorded traces, and
> `--agent` runs your own agent under specdeck's loop. Phase 2 — the provider × prompt
> matrix and cost — is next; coverage, calibration, and drafting come after. Anything
> below described in the future tense is a target, not shipped behavior. Progress is
> tracked in [milestones](https://github.com/jacquardlabs/specdeck/milestones), one per
> phase; the product definition is in [PRODUCT.md](PRODUCT.md) and decisions land in
> [DECISIONS.md](DECISIONS.md).

## What runs today

```console
$ uv run specdeck run cards/basic-economy-return-change.md \
    --trace cards/traces/basic-economy-return-change.otlp.json

  gate     PASS   1/1 runs   (passes at 1)
  credit   4/4   (over 1 passing run)

  variance n/a — 1 passing run, a spread needs two
  latency  p50 3.87s, p95 3.87s over 1 run
  cost     ~$0.0014 estimate (rates as of 2026-08-24), agent tokens only, 1 run

  wires, run 1 of 1
    ok   never:update_reservation_flights  0 occurrences
    ok   at_most:search_direct_flight      0 calls, budget 2
    ok   latency                           3.87, under 120
    ok   stop_reason                       0 occurrences

  criteria, run 1 of 1
    ok   The agent looks up the reservation, recognises it is basic economy, and…
    ...

  judge claude-sonnet-5 (replayed), 1 call over 1 run
```

One of five τ-bench airline cards, its wires evaluated against a raw OTLP export, its
prose graded by a pinned judge replaying a recorded cassette. No API key, no network. The
trace is a real OpenTelemetry GenAI export rather than a specdeck-shaped file, because an
agent already emitting OTel needs no adapter.

The three dim figures qualify the two above them and never replace them. The dollar amount
prices the agent's own traced tokens off a rate table shipped in the package — an estimate
carrying the date it was checked, never a billing figure. `specdeck rates` prints that
table; `--rates`, or a `rates.toml` beside the card, corrects it. A run that wastes tokens
gets a `waste` block under the detail; a finding says what it cost and never changes the
verdict.

Exit codes are distinct: `0` the cell passed, `1` it failed, `2` the run could not start,
`3` specdeck itself broke. A caller reading only the code should never route a malformed
lockfile to the SME as an eval regression.

### A card's first run

A new card is unpinned and unrecorded, and specdeck refuses both rather than guessing.
Once, with a key in the environment:

```console
$ specdeck run cards/your-card.md --trace run.otlp.json --relock --live
```

`--relock` writes `spec.lock.toml` beside the card, pinning the judge model and hashing
the rubric, the compiled wires, and the simulator prompt. `--runs` defaults to one per
`--trace`. `--live` calls the judge and records the reply into `cassettes/` beside the
card — once, unless the reply carries no gradable verdict, in which case it resamples up
to three times and records the one that parsed. Every run after that needs neither flag
and makes no network call.

The two pins invalidate on different edits, on purpose. The lockfile goes stale when the
prose, a criterion, a weight, or a **wire** changes — anything the card asserts. A
cassette is keyed on the judge prompt, so it goes stale when the prose, a criterion, the
policy, or the trace changes; wires never enter that prompt, so editing one costs no
recording.

### Running the agent

```console
$ specdeck run cards/your-card.md --agent yourpkg.adapter:Agent \
    --vocabulary cards/vocabulary.txt --runs 5 \
    --relock --simulator-model claude-sonnet-5 --live   # first run only
```

Your agent implements one protocol — `async run(messages, tools, config) -> events`, plus
an optional `describe()`. The adapter returns the events, so the trace is whatever the
agent actually did, including tool calls the loop never sees. A simulated user plays the
card's `simulator:` intent and stamps `specdeck.marker` on the turns it disagrees with;
its model is pinned in `spec.lock.toml` alongside the judge's, because simulator
benevolence bias shifts pass rates with no card change.

Simulator turns record into the same `cassettes/` directory as the judge, under a
`simulator-` prefix, so a conversation replays without a key once recorded. Later runs
need none of the three flags above.

### Lint

```console
$ uv run specdeck lint cards --lock cards/spec.lock.toml --vocabulary cards/vocabulary.txt
```

Zero tokens, no network. Checks structure, dead fixture and policy paths, lockfile
freshness, wire syntax, unknown measures, and contradictory or redundant wires, and
reports cassettes no card owns. Given a declared vocabulary it also catches wires naming a
tool or a marker that does not exist. A rule that lacks the data it needs reports itself
**skipped** rather than passing quietly — `specdeck lint cards` on this repo prints one
today, because detecting a cassette whose prompt has moved needs the trace that produced
it.

One rule reads inside the prose block and only one: prose describing the card's own
pass/fail machinery makes the judge answer with commentary instead of a verdict. It warns.
Nothing else about the SME's wording is lint's business.

Runs in pre-commit and in CI, from the same command.

Not yet: the provider × prompt matrix, the definition-fed and prose-aware lint groups, and
the `eventually` and precedence patterns — the palette ships `never`, `at_most`, bounds,
and after-K-then-Y. specdeck's own judge and simulator speak the Anthropic Messages API
only; they sit behind one `complete()` seam, and a second provider is
[#60](https://github.com/jacquardlabs/specdeck/issues/60).

## Why

Model migrations break prompts silently. The Claude 4.8→5 upgrade inverted prompt
scaffolding, squeezed `max_tokens` under default thinking, and raised token counts ~35%
via a new tokenizer. Teams validated in production because no model-independent spec of
their app's behavior existed.

Prompts encode workarounds. Cards encode intent. A card survives the model swap because
it never mentions the model.

## The card

One markdown file per scenario, in your repo, reviewed in PRs.

```markdown
# Scenario: refund request on basic economy
context:
  fixture: airline_seed.json
  policy: airline.md
  simulator: "frustrated customer wants a refund on flight F1234"

The agent refuses the change, clearly explains the basic economy
restriction, and proposes cancel-and-rebook as an alternative.
It never promises an exception.

wire:
  - modify_reservation: never
  - web_search: at_most 2
  - escalate_to_hitl: after 5 non_agreement
  - latency: under 120s
  - stop_reason: not truncated

credit:
  - "tone remains apologetic and professional": 2
  - "explains the fare rule in plain language": 1
  - wire: response_tokens under 400: 1
```

Two owned zones. The prose block is the domain expert's — it becomes the judge prompt
verbatim, hashed into the lockfile. The `wire` block is the developer's — deterministic
constraints over the execution trace, hashed separately so a stale lock says which half
moved. Routing PR review by zone is the intent; there is no CODEOWNERS file yet.

A prose-only card runs immediately, judge-only. Wires are never a prerequisite.

Full spec: [docs/card-format.md](docs/card-format.md).

## How it will report

Every check carries a tier. **Gate** checks define pass and block. **Credit** checks are
weighted, reported, and never blocking. A cell reports two numbers, never blended: gate
pass rate over N runs, and credit score conditional on pass. Credit never offsets a
failed gate.

Judge model, rubric text, compiled wires, and the simulator are hash-pinned in
`spec.lock.toml`. An unpinned judge is not a test.

Measurement model: [docs/measurement.md](docs/measurement.md).

## Where this sits

Intent, not a claim about shipped behavior:

| Tool | What it specs | What specdeck intends to add |
|---|---|---|
| promptfoo | per-output asserts in YAML | conversation-level scenarios, trace-temporal wires, statistical cells, calibration ledger |
| Inspect | Python `Task`s for eval engineers | an SME-facing spec layer with its own runner; Inspect logs are an accepted trace source |
| LangWatch Scenario | a code DSL for developers | a card a non-developer edits |
| Braintrust / LangSmith | a spec that lives in their database | a spec that lives in your repo |

## License

MIT. See [LICENSE](LICENSE).

The demo cards under `cards/` derive from [τ-bench](https://github.com/sierra-research/tau-bench)
(MIT, © 2024 Sierra) — the airline policy verbatim, the fixtures as slices, the scenario prose
adapted from its task instructions. See [NOTICE](NOTICE).
