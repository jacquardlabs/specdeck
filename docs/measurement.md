# Measurement

What a run reports, how much of the system the suite actually touches, and how we know the
judge still means what the SME meant.

**Status.** The single cell ships: gate pass rate over N runs, ≥k-of-N, credit score
conditional on pass, and beneath them variance, latency p50/p95 and a dollar estimate
([#52](https://github.com/jacquardlabs/specdeck/issues/52)), with the two ported waste
classifiers ([#23](https://github.com/jacquardlabs/specdeck/issues/23)). So do the token
baseline and the JUnit report ([#17](https://github.com/jacquardlabs/specdeck/issues/17),
[#18](https://github.com/jacquardlabs/specdeck/issues/18)). So does coverage, behind
`specdeck coverage`: the policy inventory, the vocabulary table, and path coverage at the
introspection depth reached. Mutation scoring and the calibration ledger do not. See
[DECISIONS.md](../DECISIONS.md).

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

## The token baseline

`spec.baseline.toml`, beside `spec.lock.toml`, records what each card's cell cost in output
tokens. The built-in `token_baseline` wire bounds a run at that figure plus 10%, so a card
that starts costing materially more fails instead of passing quietly at four times the
price. The full shape is in [card-format.md](card-format.md#the-token-baseline); what
belongs here is what the number means.

**It is a median, taken over the runs of the cell** — the lower of the two on an even
count, so the recorded figure is a token count some run actually produced. A mean moves
with one spike; a max ratchets upward on the worst run ever seen and never comes back down.

**It is `total_output_tokens`, the same measure the bound reads.** Not the per-model
`usage_by_model` table the cost estimate groups by: a baseline the bound never reads is a
baseline that cannot fire, and a two-model cell must not have its models averaged into one
figure by accident.

**The tolerance is 10%, chosen and not derived.** There is no measurement behind it. A run
that exceeds the baseline by exactly the tolerance has not exceeded it, and the allowance
is floored to a whole token before the bound is set, because no run costs half a token.

**No baseline recorded gates nothing.** A repo that has never run `--update-baseline` gets
no regression wire at all — a first install runs green, and gating on a number nobody wrote
down would mean inventing one. Once a baseline exists the bound fails closed: a trace that
reports no `gen_ai.usage.output_tokens` reds the card naming the attribute, which is the
same rule every other token figure follows.

**A recorded baseline is positive at both ends.** `--update-baseline` refuses, and writes
nothing, when any run reported no `gen_ai.usage.output_tokens` *or* reported them totalling
zero — two different facts, two different messages, and neither may be recorded. The reader
refuses the same figure, so a hand-edited `output_tokens = 0` reports itself as a user error
rather than as an internal one. Nothing can be recorded, or committed, that makes the gate
useless: a bound of zero is not a bound the runner will hold.

Recording and gating happen in the same run: `--update-baseline` folds the fresh median
into this run's own bound, exactly as `--relock` records a lock and then verifies against
what it just wrote. A run costing more than the median by more than the tolerance therefore
fails the invocation that recorded it — and whether the *cell* fails with it is arithmetic
rather than judgement, so it is stated here rather than left to be discovered.

`median_low` leaves ⌈N/2⌉ runs at or below the recorded figure, and the default threshold
is k = min(3, N). At N ≥ 5 those two always meet: five runs give three at or below the
median against a threshold of three, which passes with no margin at all. Below five they do
not — N=2 and N=3 need every run, and N=4 needs three of four while two may sit above the
median. **So a cell whose runs disagree by more than the tolerance, recorded from fewer
than five traces, fails the invocation that recorded it and keeps failing**: re-recording
computes the same median from the same runs. That is the spread being reported, not a
contradiction. Run the cell at N ≥ 5, where the k-of-N statistic absorbs exactly the two
runs above the median it was chosen to absorb, or treat a token cost that swings more than
10% as the finding.

The file is written only once the cell has actually run, so a run refused before that — a
trace count that disagrees with `--runs`, a missing cassette — leaves a committed baseline
untouched rather than overwriting it with a number from a cell that never ran. A path named
with `--baseline` that cannot be written exits 2 after the report has printed, the rule
`--junit-xml` and `--rates` already follow.

A run whose gate then *fails* does still set a baseline. That is not refused, because
whether a failing run may record one is a product question nobody has answered, and
refusing would answer it. It is never silent: the run prints a note saying the baseline came
from a failing run and should be re-recorded once the card passes. Unlike a rubric hash, a
cost baseline is a measurement of behaviour, and recording the cost of behaviour you do not
want is the hazard the note exists to surface.

## The CI report

`specdeck run --junit-xml PATH` writes a JUnit document, on pass and on fail alike. The
mapping is deliberate and stated, because other tools parse it: `<testsuites>` is the
invocation, `<testsuite>` is the cell, `<testcase>` is one run of it, and `<failure>` names
the wires and criteria a run failed.

One row per run rather than one per card, because a report a human can act on has to say
*which* run broke and on what. The cost: a cell passes at k of N, so a tolerated failure is
a red row beside exit 0. Every failure message carries the k-of-N it was judged against —
"run 3 of 5 failed; the cell needs 4 of 5 and got 4" — and the suite's `system-out` repeats
the cell's own verdict, so nothing has to be inferred from a count of red rows.

It is written as UTF-8, because the document's own declaration says `encoding='utf-8'` and
every summary line carries an em dash. Left to the host's locale, a non-UTF-8 default either
kills a passing run or hands CI bytes that contradict the declaration.

Nothing is written when the run never produced a cell (exit 2 or 3). An empty green suite
for a run that never started would be a lie, and some renderers read a missing file as
"nothing to report" — the loud exit code is the signal in that case, not the file.

A token regression is not a new exit code. It is a gate wire, so it exits 1 like any other
failed gate.

## Coverage

Two tiers, never blended into one percentage.

**Structural coverage** is deterministic against the agent definition — the definition-fed
lint obligations in [card-format.md](card-format.md). Binary and non-gameable.

**Semantic breadth** is judge-assessed and report-only.

Runtime denominators, each landing in its own phase:

| Denominator | Phase | What it counts |
|---|---|---|
| **Policy** | 2 | Clauses extracted from the policy documents named in `context`, reported as an **inventory** — count per document, count per section, and any document no card names. Not clauses × cards: see below. |
| **Vocabulary** | 2 | Tools with no wire and no exercising scenario, against the declared vocabulary. |
| **Path** | 2 | Agent-graph edges no run hit, at the introspection depth actually reached. |
| **Production intent** | 4–5 | Clusters over ingested OTLP production traces, mapped to cards. Uncovered intents feed the `specdeck draft` queue. |

`specdeck coverage [PATHS] --vocabulary <file> --trace <file>` prints all three, each as
its own table. Traces are pooled across the deck, so the vocabulary and path tables answer
a suite-level question and cannot say *which* card exercised what; that waits on
[#70](https://github.com/jacquardlabs/specdeck/issues/70). `specdeck run` prints the path
table for the run it just did, and not the other two — a single card cannot answer a
suite-level denominator, and printing "1 of 14 tools wired" for one card would read as 7%
coverage of a five-card deck.

Every table names what it could not see rather than reporting 0% or 0 of 0: no vocabulary
means no denominator, no traces means exercising was not checked, and a policy document
written as paragraphs has no clauses to count. A file under the deck that does not read as
a card is listed above the tables and does not stop them — `specdeck lint` owns the `parse`
error and the exit code it carries.

**A document no card names** is found by looking beside the ones cards do name: every other
`.md` in a directory holding a named policy. A card there is skipped only when the
directory also holds a card that names a policy — the layout where the two genuinely live
together. So a deck keeping policies in their own directory gets the signal, and one
keeping them beside the cards gets no rows rather than a row per card.

**Path coverage understates, and says so on every line that carries a figure.** The
denominator comes from introspection and never from the trace — a trace records what
happened, so edges derived from it would make denominator equal numerator and read 100%
forever. The numerator is a recorded interim, in the same spirit as `bound` in the property
IR: nothing in the trace schema carries graph-node identity, so a node is matched to a tool
name and an edge counts as hit when two consecutive `execute_tool` spans carry its two
names. A graph whose nodes are routers, chat steps or hand-offs therefore declares edges no
trace can ever mark hit. The eventual fix is a reserved `specdeck.node` attribute the
agent's own instrumentation stamps, exactly as `specdeck.marker` already is
([#89](https://github.com/jacquardlabs/specdeck/issues/89)); it is deferred because every
existing trace lacks it. Recorded 2026-08-25.

**The policy table is an inventory, not a matrix.** The shape above once read "clauses ×
cards", and no predicate exists that decides a card exercises a *clause*: a card's `context`
names a document. The only attribution available today is document-level, which would put
an identical mark in every cell of a document's rows and be misread as per-clause
attribution. The matrix is deferred until a predicate exists
([#88](https://github.com/jacquardlabs/specdeck/issues/88)), and the clause count is a
denominator you read rather than a matrix you fill. Recorded 2026-08-25.

**Coverage percentages never gate CI.** The one exception is the per-feature definition
obligations, which are binary and cannot be gamed by adding cards. `specdeck coverage`
exits 0 on any computed result — 2 for a broken input, 3 if specdeck itself breaks, never
a code derived from a figure — and there is deliberately no `--fail-under`.

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
it passed. The baseline it would be compared against now exists — see **The token
baseline** below — but the comparison is not folded into a waste finding: the built-in
`token_baseline` wire already fails the run, and a finding that duplicated a gate would
report the same fact twice at two severities. The waste block stays absolute quantities.
