# specdeck

Executable behavioral specs for LLM systems. A domain expert writes a prose criterion.
A developer wires deterministic constraints under it. The runner executes the resulting
cards against a provider × prompt matrix and reports pass rate, cost, and judge drift.

> **Status: Phase 1, walking skeleton.** Five cards run end to end against recorded
> traces. Everything else below — the provider matrix, coverage, calibration, drafting —
> describes the target surface, not shipped behavior. Progress is tracked in
> [milestones](https://github.com/jacquardlabs/specdeck/milestones), one per phase; the
> product definition is in [PRODUCT.md](PRODUCT.md) and decisions land in
> [DECISIONS.md](DECISIONS.md).

## What runs today

```console
$ uv run specdeck run cards/basic-economy-return-change.md \
    --trace cards/traces/basic-economy-return-change.otlp.json --runs 1 --pass-threshold 1

  gate     PASS   1/1 runs   (passes at 1)
  credit   4/4   (over 1 passing run)
```

One of five τ-bench airline cards, its wires evaluated against a raw OTLP export, its
prose graded by a pinned judge replaying a recorded cassette. No API key, no network. The trace is a real
OpenTelemetry GenAI export rather than a specdeck-shaped file, because an agent already
emitting OTel needs no adapter.

Exit codes are distinct: `0` the cell passed, `1` it failed, `2` the run could not start.

### A card's first run

A new card is unpinned and unrecorded, and specdeck refuses both rather than guessing.
Once, with a key in the environment:

```console
$ specdeck run cards/your-card.md --trace run.otlp.json --runs 1 --pass-threshold 1 \
    --relock --live
```

`--relock` writes `spec.lock.toml` beside the card, pinning the judge model and hashing
the rubric and simulator prompt. `--live` calls the judge once and records the reply into
`cassettes/` beside the card. Every run after that needs neither flag and makes no network
call. Editing the prose, a criterion, or the trace invalidates both, on purpose.

### Lint

```console
$ uv run specdeck lint cards --lock cards/spec.lock.toml --vocabulary cards/vocabulary.txt
```

Zero tokens, no network. Checks structure, dead fixture and policy paths, lockfile
freshness, wire syntax, and contradictory or redundant wires; with a tool vocabulary it
also catches wires naming a tool that does not exist. A rule that lacks the data it needs
reports itself **skipped** rather than passing quietly. It never reads the content of the
prose block — that zone is the SME's.

Runs in pre-commit and in CI, from the same command.

Not yet: running the agent itself, the provider matrix, the definition-fed and
prose-aware lint groups, and the `eventually` and precedence patterns — the palette ships
`never`, `at_most`, bounds, and after-K-then-Y.

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
  - writer<->reviewer: escalate_to_hitl after 5 non_agreement
  - latency: under 120s
  - stop_reason: not truncated

credit:
  - "tone remains apologetic and professional": 2
  - "explains the fare rule in plain language": 1
  - wire: response_tokens under 400: 1
```

Two owned zones. The prose block is the domain expert's — it becomes the judge prompt
verbatim, hashed into the lockfile. The `wire` block is the developer's — deterministic
constraints over the execution trace. CI routes review by zone.

A prose-only card runs immediately, judge-only. Wires are never a prerequisite.

Full spec: [docs/card-format.md](docs/card-format.md).

## How it will report

Every check carries a tier. **Gate** checks define pass and block. **Credit** checks are
weighted, reported, and never blocking. A cell reports two numbers, never blended: gate
pass rate over N runs, and credit score conditional on pass. Credit never offsets a
failed gate.

Judge model, rubric text, and simulator are hash-pinned in `spec.lock.toml`. An unpinned
judge is not a test.

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
