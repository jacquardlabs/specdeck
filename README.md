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
table; `--rates`, or a `rates.toml` beside the card, corrects it — a broken table you named
stops the run, one merely found beside the card only prints a note. A run that wastes tokens
gets a `waste` block under the detail; a finding says what it cost and never changes the
verdict.

Every card also gets three wires it did not author: `stop_reason: not truncated`, a latency
budget (`--latency-budget`, default 120s), and a token regression against
`spec.baseline.toml`, which `--update-baseline` records. A card takes one back by authoring
the same subject — there is no opt-out syntax — and none of the three is hashed into the
lockfile, so a default moving in a release is not card drift. A card with no baseline
recorded gets no regression wire at all. See
[docs/card-format.md](docs/card-format.md#built-in-wires).

`--junit-xml PATH` writes a report any CI system renders: one `<testsuite>` per cell, one
`<testcase>` per run, and a `<failure>` naming the wires and criteria a run failed. Written
whenever a cell was produced, on pass and on fail; not written when the run could not start.

Exit codes are distinct: `0` the cell passed, `1` it failed a gate — including a token
regression, which is a gate wire like any other — `2` the run could not start, `3` specdeck
itself broke, `4` a matrix did not complete because its budget stopped it. A caller reading
only the code should never route a malformed lockfile, or a matrix that ran out of money,
to the SME as an eval regression.

### A card's first run

A new card is unpinned and unrecorded, and specdeck refuses both rather than guessing.
Once, with a key in the environment:

```console
$ specdeck run cards/your-card.md --trace run.otlp.json --relock --live
```

`--relock` writes `spec.lock.toml` beside the card, pinning the judge model and hashing
the rubric, the compiled wires, and the simulator prompt. `--update-baseline`, separately,
writes `spec.baseline.toml`: what this card costs in output tokens, so a later run that
costs materially more fails rather than passing quietly. `--runs` defaults to one per
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

### The provider x prompt matrix

```console
$ specdeck run cards/your-card.md --agent yourpkg.adapter:Agent \
    --vocabulary cards/vocabulary.txt --matrix cards/matrix.toml --budget-usd 5.00
```

The matrix lives in its own file, never in the card: a card names what the behaviour must
be, and a roster of providers is the developer's zone, not the SME's.

```toml
[budget]
usd = 5.00              # a hard cap; --budget-usd overrides it

[[provider]]
name = "sonnet"
model = "claude-sonnet-5"          # specdeck reads this, only to price the column
config = { endpoint = "..." }      # specdeck never reads inside `config`

[[prompt]]
name = "terse"
config = { system_prompt_path = "prompts/terse.md" }
```

The columns are the product of the two axes, named `<provider>/<prompt>`, and each one's
`config` — the prompt table merged over the provider table — is handed verbatim to your
adapter's `run(messages, tools, config)`. specdeck never looks inside it. An axis may be
left out entirely, in which case the columns are the other axis alone.

Columns run in parallel, `--matrix-concurrency` at a time (default 2, on top of
`--concurrency` runs within each column). **`--live` forces one column at a time** and says
so: the simulator's first turn builds the identical prompt in every column, so two live
columns would race one cassette. Parallelism therefore pays on replay, which is every run
after the first.

**What the cap counts, and where it can actually prevent anything.** It counts everything —
specdeck's own judge and simulator calls, and your agent's own model calls read back off
the trace — but the two are not symmetric, and the report says so rather than implying a
guarantee it cannot give:

- specdeck's own spend is genuinely prevented. Each call is checked against the cap before
  it is made, and every call costs nothing at all in replay.
- your agent's spend is only ever reactive. Its model calls happen inside `adapter.run`,
  which spends the money and reports it afterwards through the optional `input_tokens` and
  `output_tokens` on the `Chat` events it returns. **An agent conversation already under way
  can exceed the whole remaining budget before specdeck sees a token count.**
- when the cap trips, work already in flight is allowed to finish so the cassette it paid
  for is recorded. Only new work is refused — including the next run within a column, not
  just the next column — so the overshoot is bounded by what is already in flight:
  `--matrix-concurrency` x `--concurrency` of specdeck's own calls, plus the one agent
  conversation each running column is inside. It is printed rather than glossed.

The cap fails closed rather than charging zero for a run nobody can price. Under a cap, a
column whose `model` has no entry in the rate table refuses the whole matrix before
anything starts, naming the model — and so does a pinned judge or simulator the table
cannot price, since an unpriced model is charged $0.00 and the cap would never trip on
specdeck's own calls; and a run whose trace reports no `gen_ai.usage` or names
no model at all aborts the remaining columns, naming your adapter. Without a cap none of
those fire and the run is only measured.

A stopped matrix exits `4`, never `1`: the columns that did not run neither passed nor
failed, and a CI reading `1` would be told the card regressed. The grid reports every
column that was asked for, as **PASS**, **FAIL**, **skipped**, **stopped** or **error** —
never fewer columns than were declared. `--update-baseline` records a token baseline per
column rather than one for the card, so a cheap provider is not gated at an expensive
one's cost; a baseline recorded by a single-cell run sits in the `default` slot and the
run says out loud that no column inherits it.

### Coverage

```console
$ uv run specdeck coverage cards --vocabulary cards/vocabulary.txt \
    --agent-def yourpkg.graph:app --trace run.otlp.json
```

Three denominators, printed as three tables and never blended into one percentage:
**policy** (clauses per document and per section, and any policy document no card names),
**vocabulary** (declared tools with no wire and no exercising run), and **path** (agent-graph
edges no run traversed). Every table names what it could not see rather than reporting 0%:
no `--vocabulary` means no denominator at all, no `--trace` means exercising was not
checked, and a file under the deck that does not read as a card is listed above the tables
instead of stopping them. `specdeck run` prints the path table for the run it just did.

**Coverage never gates.** `specdeck coverage` exits 0 on any computed result — `2` for a
broken input, `3` if specdeck itself breaks, never a code derived from a figure — and
there is deliberately no `--fail-under`. The one exception is the definition-fed
obligations, which are binary and live in `specdeck lint`, which does gate.

Two limits the report states on every run rather than leaving to be found. The policy table
is an **inventory**, not the clauses × cards matrix: a card's `context` names a document,
not clauses, so no per-clause predicate exists yet
([#88](https://github.com/jacquardlabs/specdeck/issues/88)). And an edge counts as hit only
when two consecutive `execute_tool` spans carry its node names, so edges through router,
chat and hand-off nodes can never be marked hit and the path figure **understates** what
ran ([#89](https://github.com/jacquardlabs/specdeck/issues/89)).

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

`--agent-def <module:attribute>` points lint at your agent definition and adds the two
definition-fed obligations over the whole deck: **every cycle in the graph is bounded by a
wire naming a tool that cycle can call** (error) — the error names them, and a cycle whose
nodes bind no tool at all is reported skipped rather than errored, because no wire could
name anything inside it. And every tool binding, hand-off edge and HITL point is
named by some wire or card (warning). A LangGraph compiled graph is read by duck typing, so
langgraph is not a dependency of specdeck; anything implementing the adapter's optional
`describe()` is read too.

Every lint run prints the depth it read the definition at — `topology`, `tools`, `none`, or
`not introspected` — and each obligation reports itself **skipped** below the depth it
needs. A declared graph gives full topology and a raw-SDK loop gives tools only, so a check
that ran against half a graph must not read like one that ran against all of it. This flag
is the one lint input that imports a module of yours; zero tokens and no network still hold.

Runs in pre-commit and in CI, from the same command.

Not yet: the prose-aware lint group, the OpenAI SDK, MCP and subagent introspectors behind
`--agent-def`, the clauses × cards matrix, and the `eventually` and precedence patterns — the palette ships `never`, `at_most`, bounds,
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
