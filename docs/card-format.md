# Card format

The card is the product. This document is the spec everything else anchors to: the file
layout, the wire language under it, and the lint rules that check both.

**Status.** The four blocks, tiers, binary judge verdicts, the lockfile, and the three
built-in wires ship. The wire palette ships `never`, `at_most`, bounds, and after-K-then-Y;
`eventually` and precedence do not, and a card using one fails saying so. Lint ships its
static and vocabulary-fed rules; the definition-fed, wireable-prose, and ledger-fed groups
do not. See [DECISIONS.md](../DECISIONS.md).

## The file

One markdown file per scenario.

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

Four blocks, and a parser of roughly 50 lines. No Gherkin, no step definitions.

### `context`

What the run is set up with: fixture data, policy documents the judge and the coverage
report both read, and the simulator's opening intent.

`context` and `wire` values are palette picks from an introspected vocabulary — the tool
registry, MCP schemas, the agent roster. They are never free-typed. This is what makes
"unknown tool" a lint error rather than a runtime surprise.

### The prose block

Free text lives here and nowhere else. It becomes the judge prompt verbatim and is hashed
into the lockfile. Prose is always gate tier.

The SME owns this block. Lint never style-polices it.

### `wire`

Deterministic constraints over the trace. The developer owns this block. Wires are gate
tier unless they appear under `credit`.

### `credit`

Weighted binary checks — judge criteria or wires. The SME owns the weights. Credit runs
only after every gate has passed.

## Tiers

Every check, wire or judge criterion, is one of two tiers.

- **Gate** defines pass. A failed gate fails the cell.
- **Credit** is weighted, reported, and never blocking.

Execution order: gate wires → gate criteria → credit checks. Gate wires run first because
they are free; a card that called a forbidden tool needs no judge call.

## Judge verdicts

Binary per criterion. The judge never emits a number. A score is only ever a weighted sum
of binary verdicts.

No partial credit inside a criterion. If a criterion could be half-true, it is two
criteria.

## Wire semantics

Wires compile to a **property IR** — an intermediate representation. The wire is what the
developer writes; the property is what the engine checks. Between them sits one small
structure, and every wire in the palette reduces to it:

```
pattern × scope × event selector
```

The IR exists so that layer has exactly one shape. A wire is text, and text has to be
parsed, linted, rendered, and compared across card versions. A property is data — it
serialises, it round-trips, and it can be evaluated by something that never saw the card.
That separation is what lets the same property serve three deployment modes without the
card format changing.

- **Patterns** are the Dwyer set: `never`, `at_most`, `eventually`, after-K-then-Y,
  precedence — plus **bounds**, which compare a trace-level measure against a limit.
  `latency: under 120s` and `response_tokens under 400` are bounds, and no Dwyer pattern
  expresses them: `response_tokens` is a sum across `chat` spans, so no event selector
  reaches it. Measures are a closed set the palette owns, so lint rejects an unknown one
  without anything being declared.
- **Scopes** are `globally`, between two events, and after K occurrences.
- **Event selectors** use OTel GenAI vocabulary — `invoke_agent`, `execute_tool`,
  `retrieval`, and tool names — plus **domain events** in a reserved `specdeck.*`
  namespace, for behaviour the semconv has no place for. The escalation wire above
  triggers on `non_agreement`, and no `gen_ai.*` attribute means "the traveller
  disagreed". A marker is stamped on the span it describes: in an eval the simulator
  stamps it, and in production the agent's own instrumentation does, which is what lets
  one property serve the runtime monitor as well. Legal marker names are declared
  alongside the tool vocabulary, so an unknown one is a lint error rather than a wire
  that never fires.

This is a fixed palette, not a general logic. Anything the palette cannot express is a
gap to discuss, not a reason to widen the language.

One property compiles to three deployment modes: an eval assertion, a CI gate, and — later
— an AgentSpec-style runtime monitor. The first two ship. The IR is designed so the third
needs no format change.

## Built-in wires

Three wires every card gets without authoring them. They compile to the same property IR a
card's own wires do — one `never` and two bounds — so nothing about the report, the
evaluator, or the lockfile has a second case to handle.

| Wire | What it asserts | Where the limit comes from |
|---|---|---|
| `stop_reason` | no `chat` span finished on `max_tokens` | nothing to configure |
| `latency` | the run finished inside a budget | `--latency-budget`, default 120 seconds |
| `token_baseline` | the run did not cost much more than last recorded | `spec.baseline.toml` |

All three are gate tier and carry no weight, so they change no card's credit denominator.

**A card that authors the same subject takes its built-in back.** There is no opt-out
syntax and none is planned: `- latency: under 300s` in the `wire` block simply replaces the
default, because the merge drops any built-in whose property id an authored wire already
carries. Tier is no part of that match, so a card writing `- wire: stop_reason: not
truncated: 1` under `credit` moves the check off the gate entirely — the only way to
disable one, and deliberately something that shows up in a PR diff.

One gap, accepted rather than solved: `stop_reason: not truncated` is the only rule that
subject takes, so a card that genuinely cannot avoid truncation can demote the wire to
credit but cannot relax it.

### The token baseline

`spec.baseline.toml`, beside `spec.lock.toml` and keyed the same way, records what each
card's cell cost in output tokens:

```toml
[cards."basic-economy-return-change.md"."default"]
output_tokens = 95
```

`specdeck run --update-baseline` writes it — the median of the runs' `total_output_tokens`,
taking the lower of the two on an even count so the figure is a number some run actually
produced. It refuses, and writes nothing, when any run reported no
`gen_ai.usage.output_tokens`: a recorded 0 would bound every later run at 0.

The wire fails a run that exceeds the baseline by more than 10%. That tolerance is chosen,
not derived. **A card with no baseline recorded gets no wire at all** — a first install
runs green, and gating on a number nobody has written down would be inventing one. Once a
baseline exists the bound fails closed, so an emitter that stops reporting usage reds the
card, naming the attribute rather than a cost.

It is not in `spec.lock.toml` on purpose. The lockfile refuses and exits 2 on any drift,
and a measured token count moves on every real run.

## Lockfile

`spec.lock.toml` pins the judge model, the rubric and wire hashes per card, the simulator
model and its prompt hash, and the OTel GenAI semconv version. The runner refuses a stale
lock without `--relock`.

Wires are pinned separately from the rubric so a stale-lock error names which half of the
card moved — the prose the SME owns, or the wires the developer does. They hash from the
compiled property IR, not the wire text, so reformatting is not drift and `at_most 20` is.

The built-in wires above are **not** hashed. The hash pins what a human wrote; a default
the runner owns moving in a specdeck release would otherwise read as drift on every card
in every repo, with a `--relock` hint for a card nobody edited. An *authored* override is
a wire like any other and is hashed.

An unpinned judge is not a test.

## Lint

`specdeck lint` costs zero tokens and runs in pre-commit and CI. Rules are grouped by the
data source they need.

| Group | Phase | Rules |
|---|---|---|
| **Static** | 1 | Zone structure. Dead fixture and policy paths. Lockfile freshness. Contradictory wires. Credit weight validity. Card-mechanics language in prose (warning). Cassettes no card owns (warning). Judge and agent sharing a model family (warning). |
| **Vocabulary-fed** | 1–2 | Wires referencing unknown tools. Invalid pattern × scope combinations. |

| **Definition-fed** | 2 | Introspect the agent definition — a LangGraph graph, OpenAI SDK hand-offs, MCP configs, Claude Code subagent files. Obligations below. |
| **Wireable prose** | 4 | Countable assertions in prose → suggest a wire. Paraphrase-duplicate criteria, via local embeddings. |
| **Ledger-fed** | 3+ | Criteria with chronically low SME–judge agreement, flagged "ambiguous — reword." |

### Definition-fed obligations

- Every cycle has a bounded or escalation wire. **Error.**
- Every tool binding, hand-off edge, and HITL point is referenced by at least one wire or
  card. **Warning.**

Introspection depth varies by framework: a declared graph gives full topology, a raw-SDK
loop gives tools only. The report always states which tier it saw — an obligation check
that silently degrades is worse than one that reports its own blindness.

### Severity rule

Machine-verifiable violations are errors. Anything about the content of prose is a
suggestion. Never style-police the SME zone.

One carve-out, recorded 2026-08-24: **card-mechanics language warns.** Prose that
describes the card's own pass/fail machinery — "do not fail this card", "fails only if",
ALL-CAPS emphasis — makes the judge answer with commentary instead of a verdict, and an
ungraded criterion fails closed. That is not a style opinion; the card does not grade at
all. It warns rather than errors, because the SME's words stay theirs.

A rule may also warn, when a violation is machine-verifiable but not definitively wrong —
a card with no prose block, or a wire that restates another. And a rule reports itself
**skipped** when it lacked the data to run at all, naming what was missing. A check that
silently degrades is worse than one that reports its own blindness, and a clean report has
to mean one thing.

The tool and marker vocabularies are introspected — the tool registry, MCP schemas, the
agent roster. Until that introspection exists, `specdeck lint --vocabulary <file>` takes a
declared list under `[tools]` and `[markers]` headings, and without it `unknown-tool` and
`unknown-marker` report themselves skipped rather than passing. Measures need no
declaration: they are the palette's own closed set.
