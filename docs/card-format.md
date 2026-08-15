# Card format

The card is the product. This document is the spec everything else anchors to: the file
layout, the wire language under it, and the lint rules that check both.

Target surface — none of this is implemented yet. See [DECISIONS.md](../DECISIONS.md).

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
  - writer<->reviewer: escalate_to_hitl after 5 non_agreement
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

Wires compile to a small property IR:

```
pattern × scope × event selector
```

- **Patterns** are the Dwyer set: `never`, `at_most`, `eventually`, after-K-then-Y,
  precedence.
- **Scopes** are `globally`, between two events, and after K occurrences.
- **Event selectors** use OTel GenAI vocabulary — `invoke_agent`, `execute_tool`,
  `retrieval`, and tool names.

This is a fixed palette, not a general logic. Anything the palette cannot express is a
gap to discuss, not a reason to widen the language.

One property compiles to three deployment modes: an eval assertion, a CI gate, and — later
— an AgentSpec-style runtime monitor. The first two ship. The IR is designed so the third
needs no format change.

## Lockfile

`spec.lock.toml` pins the judge model, the rubric hash per card, the simulator model and
its prompt hash, and the OTel GenAI semconv version. The runner refuses a stale lock
without `--relock`.

An unpinned judge is not a test.

## Lint

`specdeck lint` costs zero tokens and runs in pre-commit and CI. Rules are grouped by the
data source they need.

| Group | Phase | Rules |
|---|---|---|
| **Static** | 1 | Zone structure. Dead fixture and policy paths. Lockfile freshness. Contradictory wires. Credit weight validity. Judge and agent sharing a model family (warning). |
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
