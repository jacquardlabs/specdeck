# DECISIONS

One line per decision: what was decided, and what was rejected. Newest last within a
section. A decision that turns out wrong gets a new entry, not an edit.

## Locked

| Date | Decision | Alternative rejected |
|---|---|---|
| 2026-08-15 | **Trace format** — an event log mapped to the OTel GenAI semconv: an `invoke_agent` → `chat` → `execute_tool` span tree, `gen_ai.*` attributes, content in span events. Agents already emitting OTel need no adapter. Semconv is Development status, so its version is pinned in the lockfile. | A bespoke trace schema — it would need an adapter per framework and would not accept production traces. |
| 2026-08-15 | **Agent interface** — one `AgentAdapter` protocol: `async run(messages, tools, config) -> events`, plus an optional `describe() -> {tools, edges, cycles, hitl_points}`. Raw OTLP traces are also accepted as input. | Per-framework integrations; a required `describe()` that would exclude raw-SDK agents. |
| 2026-08-15 | **Lockfile** — `spec.lock.toml` pins judge model, rubric hash per card, simulator model and prompt hash, and semconv version. The runner refuses a stale lock without `--relock`. | Trusting the judge model string alone: rubric text drifts silently, which is the failure the lockfile exists to catch. |
| 2026-08-15 | **Statistics** — a cell passes when ≥k of N runs pass (default N=5, k=4). Reports pass rate, variance, latency p50/p95, and a dollar estimate. | Single-run pass/fail — it reads nondeterminism as a regression. |
| 2026-08-15 | **Cost** — a per-provider rate table in TOML; every figure labeled an estimate. | Live pricing APIs, and unlabeled figures that read as billing. |
| 2026-08-15 | **Waste classifiers** — port cctx's retry-loop and stale-context classifiers to the trace and attribute per cell. | Writing new classifiers; the cctx ones are already validated against real sessions. |
| 2026-08-15 | **Simulator** — pinned like the judge, with its version printed in every report. | Leaving it unpinned: simulator benevolence bias shifts pass rates with no card change. |
| 2026-08-15 | **Wire palette** — Dwyer patterns (`never`, `at_most`, `eventually`, after-K-then-Y, precedence) × scopes (globally, between events, after K occurrences) × event selectors from OTel GenAI vocabulary. | A general temporal logic — unbounded surface, unteachable to an SME, and unnecessary for the patterns real cards need. |
| 2026-08-15 | **One property, three deployment modes** — eval assertion, CI gate, and later an AgentSpec-style runtime monitor. Ship the first two; design the IR so the third needs no format change. | Separate formats per mode, which would fork the spec. |
| 2026-08-15 | **License and language** — MIT. Python 3.11+, `uv`. Dependencies beyond `httpx`, `pydantic`, `typer`, and `rich` require an explicit ask. | A heavier default dependency set; a copyleft license, which would block adoption inside companies. |
| 2026-08-15 | **Parser scope** — a ~50-line parser over the card's four blocks. | Gherkin and step definitions: a second language to learn, and step definitions land the SME back in code. |
| 2026-08-15 | **Coverage never gates CI**, with one exception: per-feature definition obligations, which are binary and non-gameable. | Coverage percentage thresholds — gameable, and they punish honest denominators. |
| 2026-08-15 | **Governance** — the card format and property IR spec live in a separate repo under a permissive license, from day one. | Keeping the format inside the runner repo, which couples format stability to runner churn. |
| 2026-08-15 | **Progress tracking lives in GitHub milestones and issues**, one milestone per phase. PRODUCT.md holds personas, principles, non-goals, and kill criteria only. | A roadmap section in PRODUCT.md — it drifts from the real issue state, and nothing makes anyone notice. |
| 2026-08-15 | **Each gated phase ends in a kill-gate decision issue** (#31, #35) that closes with a recorded outcome, either way. | Letting a milestone close when its last feature issue closes — that records that work finished, not that the gate was evaluated. |
| 2026-08-16 | **Execution backend** — our own loop. The IR, judge, and trace schema are backend-independent and stay that way; Inspect logs are an accepted trace source, not the runner. One τ-bench card ran both ways over four agent scripts with zero verdict disagreement. The deciding fact: the trace comes from the adapter or from raw OTLP, so a harness transcript is never the agent's trace source — and Inspect emits no `ToolEvent` at all for a bridged agent, which is what a user-owned agent is. [Report](docs/spikes/execution-backend.md). | Compiling cards to Inspect `Task`s: it buys sandboxing for code specdeck never executes, provider portability needed at two call sites, and an audit log of a run whose trace already arrives by another route, while coupling the card format to Inspect's `Task` surface. |
| 2026-08-16 | **Domain events in the event-selector vocabulary** — a reserved `specdeck.*` attribute namespace, stamped on the span it describes, with the legal marker names declared alongside the tool vocabulary so lint rejects an unknown one. In an eval the simulator stamps them; in production the agent's own instrumentation does, which is what lets the same property serve the runtime monitor. | Domain events as first-class spans, which would put non-semconv spans in a log whose value is that it *is* the semconv; and dropping the concept, which would cost the format its escalation wire and rewrite its own example card. |
| 2026-08-16 | **Wire palette, superseding the 2026-08-15 entry** — the Dwyer patterns **plus bounds** over a fixed, introspectable measure vocabulary. `latency: under 120s` and `response_tokens under 400` are bounds, and no Dwyer pattern expresses them: `response_tokens` is a sum across `chat` spans, so no event selector reaches it. Lint rejects an unknown measure the way it rejects an unknown tool. | Dropping bounds from the card format, which would make a latency budget report-only and remove two wires from the format's own example card. |

## Deferred — decided by a spike, not by argument

None open.
