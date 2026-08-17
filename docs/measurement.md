# Measurement

What a run reports, how much of the system the suite actually touches, and how we know the
judge still means what the SME meant.

**Status.** The single cell ships: gate pass rate over N runs, ≥k-of-N, and credit score
conditional on pass. Variance, latency percentiles, and the cost estimate do not
([#52](https://github.com/jacquardlabs/specdeck/issues/52)), and neither does coverage,
mutation scoring, or the calibration ledger. See [DECISIONS.md](../DECISIONS.md).

## The cell

A cell is one card × one provider × one prompt. It reports two numbers, never blended:

- **Gate pass rate** — the fraction of N runs where every gate passed. The cell passes when
  ≥k of N pass. Defaults: N=5, k=4.
- **Credit score, conditional on pass** — the weighted sum of binary credit verdicts, over
  the passing runs only.

Credit never offsets a failed gate. A cell that scores 9/10 on credit and fails one gate
is a failing cell.

Alongside them: variance, latency p50/p95, and a dollar estimate from the per-provider rate
table. Cost figures are labeled estimates, always.

## Coverage

Two tiers, never blended into one percentage.

**Structural coverage** is deterministic against the agent definition — the definition-fed
lint obligations in [card-format.md](card-format.md). Binary and non-gameable.

**Semantic breadth** is judge-assessed and report-only.

Runtime denominators, each landing in its own phase:

| Denominator | Phase | What it counts |
|---|---|---|
| **Policy** | 2 | Clauses extracted from the policy documents named in `context`, reported as clauses × cards. |
| **Vocabulary** | 2 | Tools with no wire and no exercising scenario. |
| **Path** | 2–3 | Agent-graph edges no run has ever hit. |
| **Production intent** | 4–5 | Clusters over ingested OTLP production traces, mapped to cards. Uncovered intents feed the `specdeck draft` queue. |

**Coverage percentages never gate CI.** The one exception is the per-feature definition
obligations, which are binary and cannot be gamed by adding cards.

## Mutation score

Coverage says the suite touched something. Mutation score says the suite would have
noticed if it broke.

Mutate a recorded cassette — inject a forbidden tool call, flip the final database state,
strip the required alternative — and the suite must go red. The score is the percentage of
injected faults caught. (Phase 3.)

Mutation tests the suite. Chaos fixtures, if they ever land, test the agent. They are not
the same measurement and do not share a number.

## Calibration ledger

The judge is a measuring instrument. An uncalibrated instrument reports numbers that mean
nothing, confidently. Three channels:

1. **Sampled SME grading.** A queue of runs the SME grades by hand; agreement is tracked per
   card, per judge version.
2. **Agreement drift per judge version.** `specdeck judge upgrade` runs the old and new
   judge over the graded corpus and blocks on an agreement drop.
3. **Judge-vs-wire contradiction.** Where a wire and a judge criterion assert overlapping
   facts, disagreement is a signal about one of them. Surfaced, not silently resolved.

Chronically low agreement on a criterion is a lint suggestion: "ambiguous — reword." The
fix is the SME's, not the judge's.

**Target:** ≥80% agreement on the demo suite. Below that after rubric iteration is a Phase 3
kill criterion — the gate is [#31](https://github.com/jacquardlabs/specdeck/issues/31), and it
closes on a recorded decision either way. Rationale in [PRODUCT.md](../PRODUCT.md).

## Variance attribution

When a cell is flaky, three actors could be responsible. Pin two of {agent, judge,
simulator}, rerun the third, and report each one's share of the flake.

UNSTABLE is a verdict distinct from FAIL. Near-threshold cards go to a quarantine lane
rather than blocking, and rejoin once their variance share is attributed.

## Waste

The cctx retry-loop and stale-context classifiers, ported to the trace and attributed per
cell. A card that passes at four times the token cost of its baseline is a finding, even
though it passed.
