# specdeck

Executable behavioral specs for LLM systems. A domain expert writes a prose criterion.
A developer wires deterministic constraints under it. The runner executes the resulting
cards against a provider × prompt matrix and reports pass rate, cost, and judge drift.

> **Status: pre-Phase-1.** Nothing here runs yet. This repo currently holds the product
> definition, the card format spec, and the decision log. Every command and card shown
> below describes the target surface, not shipped behavior. Track progress in
> [PRODUCT.md](PRODUCT.md); decisions land in [DECISIONS.md](DECISIONS.md).

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
| Inspect | Python `Task`s for eval engineers | an SME-facing spec layer above it — possibly compiling to it |
| LangWatch Scenario | a code DSL for developers | a card a non-developer edits |
| Braintrust / LangSmith | a spec that lives in their database | a spec that lives in your repo |

## License

MIT. See [LICENSE](LICENSE).
