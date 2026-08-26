# What these scenarios prove, and what they cost

Draft for the migration report (#24). Numbers are measured from this repo's own runs, not
projected from a pricing page. Every dollar figure is an estimate in the sense the runner
means it: derived from a committed rate table, never from a bill.

## The question this has to answer

Six scenarios in one domain is not coverage, and nobody should pretend otherwise. So the
claim cannot be "this deck proves the agent is correct." The claim has to be narrower and
survive a hostile reading:

> A deck catches a specific class of failure that review, staging and unit tests do not,
> and it catches it before a customer does.

Everything below is either evidence for that or a limit on it.

## What actually got caught

Four findings, all from real runs in this repo. Only the first is about models.

**1. A model swap changed behaviour on identical prompts.** `claude-opus-4-8` and
`claude-opus-5` were given the same six cards and the same system prompt. They did not
score the same. Nothing about that is visible in a changelog, and nothing in a unit test
would have shown it, because the prompt did not change — only the model behind it did.

**2. Trimming a prompt is not free, and which way it cuts depends on the model.** The
grid carries a fourth column nobody asked for: the *old* model on the *trimmed* prompt.
Without it, a good result from new-model-plus-trimmed-prompt cannot be attributed to the
model rather than the trim. With it, the two effects separate. This is the difference
between "the new model is better" and "the new model needs less scaffolding", and only the
second is actionable.

**3. Both models walked past a policy boundary.** `escalation-after-repeated-refusal`
requires a hand-off to a human after the traveller has pushed back three times — a boundary
the airline policy states and the card scores. Neither model does it. Both refuse three
times, courteously, and keep going. That is a production incident waiting for a customer
to find, and it took one card to surface.

**4. The specs themselves had drifted from what they claimed.** Every card passed against
its recorded trace and 23 of 24 live cells failed, because no scenario told the simulated
traveller who they were — the recorded trace already contained the lookups, so replay never
asked. A spec that only works in replay is not a spec. This was the most valuable finding
of the exercise and it is not about any model.

Finding 4 is the honest headline. Running the deck did not primarily grade the agent; it
graded the specification, and the specification lost.

## What it costs

Measured over the second full grid — 6 cards × 4 columns × 3 runs, 72 agent runs, live:

| | |
|---|---|
| total | **$11.63** |
| per run | $0.1616 |
| per card, one model, 3 runs | $0.48 |

Projected to a real deck, at the same per-run cost:

| deck | weekly full run, one model | migration matrix, 4 columns |
|---|---|---|
| 25 cards | $12 | $48 |
| 100 cards | $48 | $194 |
| 250 cards | $121 | $485 |

**The recurring number is the small one.** A full-deck sweep of 100 cards is under $50 a
week. The matrix — the expensive column-multiplying one — is not recurring at all: you run
it when you change models or rewrite a prompt, which is a handful of times a year.

And most days you run neither. `--affected-by` maps a diff to the cards that depend on the
files it touched, so a pull request runs the two or three cards its change can reach, not
the deck: **about $1 per PR**. The deck-wide run is a nightly backstop, not the mechanism.

So the shape is: dollars per pull request, tens of dollars per week, hundreds per migration
event. Against one engineer-day of manual spot-checking — call it $600 and far narrower
coverage — the comparison is not close, and the deck does not get bored on the fortieth
scenario.

## What it does not prove

Stated here rather than left to be discovered, because a reader who finds these themselves
will discount everything above.

- **Six cards is not coverage.** The coverage tables report denominators and deliberately
  never gate CI, because a percentage that gates becomes a percentage that gets gamed.
- **One domain, one provider, one judge model.** Nothing here says how the same deck
  behaves on a different vendor.
- **The judge is itself a model.** One cell in the second grid returned no gradable verdict
  in three attempts and reported as an error rather than a result. A judge with an error
  rate is a measuring instrument with an error rate, and the report says so on its face
  rather than hiding it behind a number.
- **Three runs per cell is thin.** Cells that pass 2 of 3 are common enough that a single
  run would have reported the opposite verdict roughly a third of the time — which is the
  argument against eyeballing one transcript and calling it good, but also a caution
  against reading any individual cell here too hard.
- **Every figure is an estimate.** Priced from a committed rate table, never from a bill.
- **None of this replays.** Cassettes cover the judge and the simulator — specdeck's own
  calls — not the agent's, because the agent is the user's code and the runner records its
  trace rather than its API calls. Reproducing this report costs what it cost.

## The argument, in one line

The deck is not paying for confidence that the agent is right. It is paying for the hour in
which you find out that it is wrong — before the customer, before the migration ships, and
including the case where what is wrong is the specification.
