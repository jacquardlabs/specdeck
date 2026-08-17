# Spike: execution backend

Resolves [#2](https://github.com/jacquardlabs/specdeck/issues/2). Compiled one τ-bench
airline card to an Inspect AI `Task` and to a hand-written loop, ran both over four agent
scripts, and compared.

**Outcome: build our own loop.** Inspect is kept as an accepted trace source, not as the
execution backend. Recorded in [DECISIONS.md](../../DECISIONS.md).

Measured against `inspect-ai` 0.3.259 (2026-08-16) and
`open-telemetry/semantic-conventions-genai@main`.

## What was built

One card, ported from τ-bench airline task 1 (`olivia_gonzalez_2305`, reservation
`Z7GOZK`, basic economy, travel insurance): the agent must refuse the flight change,
explain the restriction, and offer cancellation under insurance. Five gate wires and one
credit wire, exercising `never`, `at_most`, after-K-then-Y, a duration bound, a
finish-reason check, and a token bound.

Four scripted agents drive both paths identically, so the comparison is between harnesses,
not agents:

| Script | Behaviour | Expected |
|---|---|---|
| `compliant` | looks up, refuses, offers insurance | pass |
| `violating` | searches 3×, then modifies the fare | fails `never` and `at_most` |
| `stonewalled` | holds the line through 3 pushbacks, escalates to a human | pass, after-K-then-Y non-vacuous |
| `stubborn` | same 3 pushbacks, never escalates | fails after-K-then-Y |

Path A: `Task(solver=react(...), scorer=[wires, judge], epochs=Epochs(5, at_least(4)))`,
plus an adapter from Inspect's transcript to the OTel GenAI event log. Path B: a loop that
emits the event log inline. Both then run the *same* IR evaluator and the *same* judge.

## Question 1 — port cost of the wire IR

**The IR is already backend-independent, and stayed that way.** `ir.py`, `trace.py`, and
`judge.py` import neither backend. Both paths produced identical verdicts on all four
scripts — 0 disagreements over 6 wires × 4 scripts, and identical cell verdicts, gate pass
rates, and judge-call counts.

The cost is not the IR. It is the adapter, and there are two findings about it.

**Inspect emits no OTel.** `inspect-ai` 0.3.259 contains zero references to
`opentelemetry`, and no `gen_ai.*` attribute anywhere. The event log the locked trace
decision names has to be built by hand from `ModelEvent` / `ToolEvent` — 114 sloc, written
and working. Every field the card's wires select on is present on the Inspect objects:
`ToolEvent.function`, `.arguments`, `.result`, `.agent` (the handoff target, verified in a
separate two-agent probe), `.timestamp`/`.completed`; `ModelEvent.output.usage`,
`.stop_reason`, `.model`.

**But a harness transcript is not where specdeck's trace comes from.** The locked
`AgentAdapter` protocol is `async run(messages, tools, config) -> events`: the adapter
returns the event log, and raw OTLP is accepted directly. The agent under test is the
user's, and so is its trace. Inspect's transcript could only ever be a second, parallel
record of the same run — and measuring how good that second record is settles how much
Inspect's audit log is worth.

The answer is: for a user-owned agent, not much. An external agent runs under Inspect
through `agent_bridge`, and Inspect's own source is explicit
(`inspect_ai/agent/_bridge/util.py`):

> Bridged scaffolds run their own tool calls, so no `ToolEvent` is ever emitted for them —
> tool calls live only on `ModelEvent.output.message.tool_calls`. […] Covers every bridge
> configuration.

So the full-fidelity transcript is available only for agents rewritten as Inspect agents.
A second adapter mode was written for the bridged case, synthesising `execute_tool` spans
from the model's requested tool calls. Measured over one run of `stonewalled`, spans
carrying each field:

| Field | Inspect, native agent | Inspect, bridged agent | Own loop |
|---|---|---|---|
| `gen_ai.tool.name` | 2/2 | 2/2 | 2/2 |
| `gen_ai.tool.call.arguments` | 2/2 | 2/2 | 2/2 |
| `gen_ai.tool.call.result` | 2/2 | **0/2** | 2/2 |
| `gen_ai.agent.name` | 1/1 | 1/1 | 1/1 |
| `gen_ai.response.finish_reasons` | 6/6 | 6/6 | 6/6 |
| `gen_ai.usage.output_tokens` | 6/6 | 6/6 | 6/6 |
| `specdeck.marker` | 3/6 | 3/6 | 3/6 |

All six wire verdicts are identical in both Inspect modes on all four scripts: tool names
and counts survive the bridge, because the model's requested calls carry them. Tool
results, per-tool durations, and tool errors do not. Wires asserting on those — and the
judge-vs-wire contradiction channel in the calibration ledger, which needs tool results —
would be unevaluable for the agents specdeck expects to run.

**One gap is backend-independent.** The after-K-then-Y wire triggers on `non_agreement`,
a domain event with no place in the GenAI semconv. Neither backend supplies it; the
simulator has to stamp it. Under Inspect it rides an `InfoEvent` that the adapter
re-attaches to the following `chat` span, which works but is a second mechanism to
maintain. This is an [#4](https://github.com/jacquardlabs/specdeck/issues/4) question, not
a backend question — the event-selector vocabulary needs a defined extension point beyond
`gen_ai.*`.

## Question 2 — does the judge step survive the round-trip

**Yes, on all three requirements.**

*Binary verdicts per criterion.* Inspect's `Score.value` accepts
`Mapping[str, str|int|float|bool|None]`, so one scorer returns one verdict per criterion
rather than a blended number. Both paths returned `{prose: bool, <credit criterion>: bool,
…}`.

*Gate wires before judge criteria, with the judge skipped when a gate fails.* Scorers run
sequentially in registration order and each sees the previous scorer's result on
`state.scores`, so the judge scorer short-circuits on `wires`. Measured: 0 judge calls on
`violating` and `stubborn` in both paths, 5 in both paths on the passing scripts.

*Pinning.* Judge model, rubric hash, and replay/live status ride in `Score.metadata` and
`Task.metadata` into the eval log. Nothing about the lockfile requires the runner.

Verified live against `claude-sonnet-5`, three calls, then replayed from the recorded
cassettes. The verdicts discriminate: `compliant` and `stonewalled` returned all-true with
per-criterion reasons; a control call on `violating` — which a gate wire normally
short-circuits — returned all-false and named the fabricated exception. Two notes for
[#8](https://github.com/jacquardlabs/specdeck/issues/8): `temperature` is rejected outright
on this model (`400: temperature is deprecated for this model`), so a pinned judge pins
model and rubric text, not a sampling setting; and the response carries a thinking block
before the text block, so the parse has to select by block type.

One genuine Inspect win: `Epochs(5, at_least(4))` is a direct expression of the locked
k-of-N statistic, reducing dict-valued scores per key. Written by hand it is about eight
lines.

## Why the answer is still "our own loop"

Inspect's three headline offers land differently once the agent is the user's:

- **Sandboxing** protects the harness from agent-executed code. specdeck does not execute
  the agent's code; the adapter does. This is the user's sandbox, not specdeck's.
- **Provider portability** matters to specdeck at exactly two call sites — the judge and
  the simulator. The provider × prompt matrix varies the *agent's* model, which the
  `AgentAdapter.run(messages, tools, config)` signature already delegates. Two call sites
  over `httpx` is not worth 82 transitive packages against a four-dependency budget.
- **Audit logs** would record a run whose trace already arrives by another route — from the
  adapter, or as raw OTLP — in a second format, at the fidelity measured above.

Against that: the bridged-agent `ToolEvent` gap removes tool results from the trace in
specdeck's normal case, and Inspect's `Task` surface becomes a constraint on how the card
format may evolve — the cost the deferred entry named.

Code written, by category (non-blank, non-comment, non-docstring):

| | sloc | |
|---|---|---|
| Shared | 435 | trace schema, IR, card parser, judge, agent scripts — unchanged between paths |
| Inspect-only | 299 | adapter 114, Task + scorers + tool defs + log extraction 185 |
| Own-loop-only | 157 | span emission 78, cell execution 52 |

The own loop's 157 is a floor, not an estimate. It does not include provider plumbing,
retries, rate limits, concurrency across cells, log persistence, or a viewer. Most of that
is Phase 2 work under either answer; the judge and simulator are the only model call sites
it has to cover.

## What this does not decide

Inspect remains an accepted **input**. The 114-line adapter works, and ingesting `.eval`
logs as a trace source alongside raw OTLP is a later, additive question — not gated by this
entry, and not a dependency: an `.eval` file is a ZIP of JSON entries compressed with zstd
(method 93), read here with a zstd-capable ZIP reader and no `inspect_ai` import.

## Reproducing

The spike code is not committed (issue #2 is a decision, not a deliverable). It lives in
the session scratchpad: `spike/` with `path_a_inspect.py`, `path_b_own.py`, `compare.py`,
`probe_handoff.py`, and a `specdeck_min/` package. `python compare.py` prints the
agreement table, the fidelity table, and the sloc counts, and writes `compare.json`.

Judge verdicts replay from cassettes recorded live against `claude-sonnet-5` (three calls,
credentials from Doppler `playground/dev`): `doppler run -p playground -c dev -- python …`
with `live=True`.
