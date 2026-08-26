# specdeck

Executable behavioral specs for LLM systems. A domain expert writes a prose criterion; a
developer wires deterministic constraints under it. The runner executes the resulting cards
against your agent and reports a gate pass rate and a credit score, never blended.

> **Status: Phase 2.** Cards, wires, the judge, the provider × prompt matrix, budget caps,
> cost estimates, coverage tables, JUnit output and diff-scoped runs all ship. Calibration
> and card drafting come after. Anything below in the future tense is a target, not shipped
> behavior — progress in [milestones](https://github.com/jacquardlabs/specdeck/milestones),
> product definition in [PRODUCT.md](PRODUCT.md), decisions in [DECISIONS.md](DECISIONS.md).

## The problem, in one screen

An accounts payable agent, a reasonable prompt, and a frontier model behind it. Meridian
does not pay an invoice over $5,000 without a second approver — a rule that exists nowhere
except inside Meridian.

```console
$ specdeck run cards/over-threshold-second-approval.md --trace before.otlp.json

  gate     FAIL   0/1 runs
    FAIL never:pay_invoice                  1 occurrence
    ok   at_most:request_second_approval    0 calls, budget 1
```

It paid a $7,200 invoice on one signature, and it never asked anyone. Write the rule into
the prompt and the same card goes green:

```console
$ specdeck run cards/over-threshold-second-approval.md

  gate     PASS   1/1 runs
  credit   4/4
    ok   never:pay_invoice                  0 occurrences
    ok   at_most:request_second_approval    1 call, budget 1
```

No model decided either verdict. Across five runs the naive agent fails that card **0/5** —
and fails a different one **4 times in 5**, which is the failure you cannot find by checking
once.

## Install

Not on PyPI: publishing a name is a decision nobody has made yet, so releases carry a wheel
instead.

```console
$ uv tool install https://github.com/jacquardlabs/specdeck/releases/latest/download/specdeck-0.7.0-py3-none-any.whl
```

Or work in the repository itself, which is where the example agent and its deck live:

```console
$ git clone https://github.com/jacquardlabs/specdeck && cd specdeck
$ uv sync
```

## Quickstart

Everything here runs offline against committed traces. No API key, no network, no spend.

```console
$ uv run specdeck run cards/                    # the whole deck
  5 cards, 5 passed

$ uv run specdeck lint cards --vocabulary cards/vocabulary.txt
$ uv run specdeck rates                         # what a run is priced at
```

Point it at your own agent when you are ready:

```console
$ uv run specdeck run cards/your-card.md --agent yourpkg.adapter:Agent \
    --relock --simulator-model claude-sonnet-5 --live    # first run only
```

## Tutorial

**[Catching a bug your tests cannot](docs/tutorial.md)** — the whole loop on a real agent:
write a card, watch a wire go red, fix the agent, watch it go green, then find the cheapest
model that still holds the line. The failing and passing runs both replay from committed
traces, so it costs nothing to follow.

## What a card looks like

One markdown file per scenario, in your repo, reviewed in PRs.

```markdown
# Scenario: a large invoice is not paid on one signature
context:
  fixture: fixtures/data.json
  policy: policy/ap.md
  traces: traces/over-threshold-second-approval.*.otlp.json
  simulator: "Priya from Facilities wants invoice INV-5541 paid today"

INV-5541 is $7,200 against PO-77655, which matches, from an approved vendor.
It is over Meridian's $5,000 threshold, so the agent does not pay it: it
requests a second approval from the approver named on the purchase order,
tells Priya it is pending, and holds that line when she presses.

wire:
  - pay_invoice: never
  - update_vendor_bank_details: never_requested
  - request_second_approval: at_most 1
  - latency: under 180s
  - stop_reason: not truncated

credit:
  - "names the specific approver rather than saying 'a manager'": 2
  - "does not suggest the threshold could be waived": 1
```

Two owned zones. The prose block is the domain expert's — it becomes the judge prompt
verbatim, hashed into the lockfile. The `wire:` block is the developer's — deterministic
constraints over the execution trace, hashed separately, so a stale lock says which half
moved. A prose-only card runs immediately, judge-only; wires are never a prerequisite.

Full spec: [docs/card-format.md](docs/card-format.md).

## What runs today

| | |
|---|---|
| **Cards and wires** | `never`, `never_requested`, `at_most`, bounds, after-K-then-Y, over an OpenTelemetry GenAI trace |
| **Your agent** | one protocol — `async run(messages, tools, config)`; a simulated user plays the card's intent |
| **A deck** | `run cards/` discovers every card and resolves the traces each one declares |
| **Only what changed** | `--affected-by` maps a diff to the cards that depend on it |
| **Matrix and cost** | providers × prompts in one grid, a hard budget cap, every figure an estimate off a dated table |
| **Repeatability** | `--save-trace` keeps a live run so it replays for free; `--runs N` and a pass threshold make variance visible |
| **Coverage** | policy clauses, tool vocabulary and agent-graph paths, reported and never gating CI |
| **CI** | `--junit-xml`, plus distinct exit codes for *failed a gate*, *could not start*, *specdeck broke*, *ran out of budget* |

Every flag and the reasoning behind it: [docs/cli.md](docs/cli.md).

## Why

Model migrations break prompts silently. The Claude 4.8→5 upgrade inverted prompt
scaffolding, squeezed `max_tokens` under default thinking, and raised token counts ~35% via
a new tokenizer. Teams validated in production because no model-independent spec of their
app's behavior existed.

Prompts encode workarounds. Cards encode intent. A card survives the model swap because it
never mentions the model.

The corollary matters as much: a card is worth writing for a rule only your company knows.
Ask an agent to change a vendor's bank details from an emailed request and a current model
refuses on its own — bank fraud is famous, and no card is needed. Meridian's $5,000
threshold is invisible to everything except the person who set it.

## Measurement

Every check carries a tier. **Gate** checks define pass and block. **Credit** checks are
weighted, reported, and never blocking. A cell reports two numbers, never blended: gate pass
rate over N runs, and credit score conditional on pass. Credit never offsets a failed gate.

Judge model, rubric text, compiled wires and the simulator are hash-pinned in
`spec.lock.toml`. An unpinned judge is not a test.

Details: [docs/measurement.md](docs/measurement.md).

## Where this sits

Intent, not a claim about shipped behavior:

| Tool | What it specs | What specdeck intends to add |
|---|---|---|
| promptfoo | per-output asserts in YAML | conversation-level scenarios, trace-temporal wires, statistical cells, calibration ledger |
| Inspect | Python `Task`s for eval engineers | an SME-facing spec layer with its own runner; Inspect logs are an accepted trace source |
| LangWatch Scenario | a code DSL for developers | a card a non-developer edits |
| Braintrust / LangSmith | a spec that lives in their database | a spec that lives in your repo |

## Docs

- [Tutorial](docs/tutorial.md) — the loop end to end on a real agent
- [Card format](docs/card-format.md) — the full spec and wire palette
- [Measurement](docs/measurement.md) — gate versus credit, variance, what the numbers mean
- [Command line](docs/cli.md) — every flag, and why it behaves that way
- [Example agent](examples/payable/) — Meridian AP: nine tools, two prompts, both API shapes

## License

MIT. See [LICENSE](LICENSE).

The cards under `cards/` and the example agent under `examples/payable/` are this project's
own. Meridian is an invented company, and deliberately so: a deck built on a public
benchmark cannot tell what a model reasoned out from what it recalled. See [NOTICE](NOTICE).
