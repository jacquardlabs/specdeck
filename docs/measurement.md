# Measurement

What a run reports, how much of the system the suite actually touches, and how we know the
judge still means what the SME meant.

**Status.** The single cell ships: gate pass rate over N runs, ≥k-of-N, credit score
conditional on pass, and beneath them variance, latency p50/p95 and a dollar estimate
([#52](https://github.com/jacquardlabs/specdeck/issues/52)), with the two ported waste
classifiers ([#23](https://github.com/jacquardlabs/specdeck/issues/23)). Coverage, mutation
scoring, and the calibration ledger do not. See [DECISIONS.md](../DECISIONS.md).

## The cell

A cell is one card × one provider × one prompt. It reports two numbers, never blended:

- **Gate pass rate** — the fraction of N runs where every gate passed. The cell passes when
  ≥k of N pass. Defaults: N=5, k=4.
- **Credit score, conditional on pass** — the weighted sum of binary credit verdicts, over
  the passing runs only.

Credit never offsets a failed gate. A cell that scores 9/10 on credit and fails one gate
is a failing cell.

Alongside them, three secondary figures. They qualify the two numbers rather than
competing with them, which is why the report prints them beneath and dim, not beside.

- **Variance** — the spread of per-run credit over the *passing* runs: the min-max range
  and the population standard deviation, taken over exactly the runs `credit_mean` is taken
  over, so the two can never disagree. A cell printing "credit 3/3 over 4 passing runs"
  hides the sequence 1, 3, 3, 5; this is the line that says which it was. Below two passing
  runs it reads `n/a` naming the count, rather than reporting sd 0.0 for a set with nothing
  to compare. When the gate itself went both ways the line adds "gate mixed, 4 pass / 1
  fail"; when every run agreed it stays quiet, because the headline already said so. It is
  not a verdict — UNSTABLE and the quarantine lane are the later variance-attribution work
  below, and nothing here touches the exit code.
- **Latency p50/p95** — over the N runs' end-to-end `invoke_agent` durations, which is
  exactly the measure a card's `latency: under 120s` wire bounds, so the report and the wire
  cannot disagree about what was timed. Percentiles interpolate linearly between the order
  statistics at (n−1)q. Over five runs a p95 is the fourth-largest sample leaning on the
  maximum and is no kind of tail estimate, so the sample count is printed with it, always.
- **A dollar estimate** — the agent's traced tokens, priced through the rate table below.
  Agent tokens only, and the line says so: specdeck's own judge and simulator calls return
  bare text and their cassettes record no usage, so pricing that spend would mean inventing
  it ([#80](https://github.com/jacquardlabs/specdeck/issues/80)). A model whose chat spans
  reported no usage is not charged zero for it — the line reads `n/a` naming `gen_ai.usage`,
  the same way an unpriced model reads `n/a` naming the model. A model that reported one
  half and not the other reads `incomplete gen_ai.usage`, not `no`: it cannot be priced,
  and it did emit the attribute.

Cost figures are labeled estimates, always, and no figure here moves the verdict.

### Cost estimates

The rate table is `src/specdeck/rates.toml`, shipped inside the package: per-provider,
per-model USD per million input and output tokens, keyed by model family. A key prices its
family and that family's dated ids — `claude-sonnet-5` prices `claude-sonnet-5-20260514` —
and nothing else: a sibling that merely extends the name, like `claude-opus-4-9` over
`claude-opus-4`, reports `n/a` rather than inheriting the neighbouring price. It carries a
`verified` date — the day those figures were last checked against the vendor's own pricing
page — and every figure derived from it is printed with that date and the word *estimate*.

A `rates.toml` beside the card, or one named with `--rates`, is merged over the built-in
one: it adds or corrects entries without restating the table, and it must carry its own
`verified` date. The merged table prints the older of the two dates, because an override
that adds one model says nothing about the built-in rows and so cannot re-date them.

`specdeck rates` prints the table that resolved, reading an override from `--rates` or
from the directory it is run in. It takes no card. `specdeck run` does, and resolves its
override against the card rather than the working directory — `--rates`, else a
`rates.toml` beside the card — the same way it already finds the lockfile and the
cassettes, so where you stand cannot change which table priced a run.

The two paths fail differently, on purpose. A table named on `--rates` is part of the
invocation, so a broken one stops the run with exit 2. One merely *found* beside the card
is optional and prices a secondary figure: a broken one prints a note naming the file and
the run continues on the built-in table, which carries its own `verified` date. Aborting
an otherwise-clean eval over an unrequested file would report an infrastructure failure
for a card that would have passed.

Two limits belong to the table rather than to the runner. A model with no entry reports
`n/a` naming the model — never $0.00, and never a substituted default rate, because an
invented figure under an "estimate" label is still an invented figure. And there is no
cache pricing: the semconv carries only `gen_ai.usage.input_tokens` and
`gen_ai.usage.output_tokens`, so a prompt-cached run is over-estimated, its cache reads
charged at the full input rate.

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
cell. Ported, not rewritten: the detection logic and every threshold — 2,000 estimated
tokens to be a staleness candidate, 5 spans past the last reference before it is stale,
HIGH severity at four failures, 3-gram overlap for a reference — are carried across as
validated against real sessions, and only the input adapter is new. cctx re-pairs a tool
call with its result across two JSONL turns; one `execute_tool` span holds both, so that
pairing is deleted rather than reimplemented.

One counting unit changed and the numbers deliberately did not. cctx counts turns, where a
tool call is two of them; specdeck counts span ordinals, where it is one. Five span
ordinals is therefore up to twice as strict over a tool-heavy stretch as cctx's five turns.
Rescaling a threshold validated against real sessions would be a rewrite wearing a port's
clothes, so it stays as written until a real trace misfires.

**A finding is never a gate.** `Cell.passed` and the exit code are untouched. The report
prints one line per distinct finding with the number of runs that produced it, worst
severity first, then one total per kind, labeled an estimate and stated across the runs it
covers — or "not reported by the trace" when no chat span carried usage, never 0.

One total per kind, not one total: a retry loop is measured in tokens (what the run
re-sent) and a stale result in token-turns (tokens carried, times the requests that carried
them). Summing the two would give a figure in no unit at all. cctx kept them apart for the
same reason, pricing each kind separately in its orchestrator rather than adding them.

A card that passes at four times the token cost of its baseline is a finding, even though
it passed. Nothing in the repo defines a card's baseline yet, so that ratio waits on
[#17](https://github.com/jacquardlabs/specdeck/issues/17); today the report gives absolute
token quantities and leaves the comparison to the reader.
