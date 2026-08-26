# What a deck proves, and what it costs

## The bet

Six scenarios in one domain isn't coverage, and nobody should pretend it is. So the claim
has to be narrower than "this proves the agent is correct," and it has to survive a hostile
reading:

> A deck catches a class of failure that review, staging and unit tests don't, and it
> catches it before a customer does.

Everything below is either evidence for that or a limit on it.

## What actually got caught

Four findings, all from real runs in this repo. Only the first is about models.

**A model swap changed behaviour on identical prompts.** Two models were given the same
cards and the same system prompt. They didn't score the same. Nothing about that is visible
in a changelog and nothing in a unit test would show it, because the prompt didn't change —
only the model behind it did.

**Trimming a prompt isn't free, and which way it cuts depends on the model.** The grid
carries a column nobody asked for: the old model on the trimmed prompt. Without it, a good
result from new-model-plus-trimmed-prompt can't be attributed to the model rather than the
trim. With it, the two effects separate. That's the difference between "the new model is
better" and "the new model needs less scaffolding," and only the second is actionable.

**Both models walked past a policy boundary.** One card requires a hand-off to a human after
three pushbacks. Neither model does it reliably — one manages it four times in five, which
is worse than failing outright, because four times in five is what you'd see if you checked
by hand. That's a production incident waiting for a customer to find, and it took one card.

**The specs themselves had drifted from what they claimed.** Every card passed against its
recorded trace and 23 of 24 live cells failed, because no scenario told the simulated user
who they were. The recorded trace already contained the lookups, so replay never asked. A
spec that only works in replay isn't a spec.

That last one is the honest headline. Running the deck didn't primarily grade the agent. It
graded the specification, and the specification lost.

## What it costs

Measured over a full grid — 6 cards × 4 columns × 3 runs, 72 live agent runs:

| | |
|---|---|
| total | **$11.63** |
| per run | $0.1616 |
| per card, one model, 3 runs | $0.48 |

Projected to a real deck at the same per-run cost:

| deck | weekly full run, one model | migration matrix, 4 columns |
|---|---|---|
| 25 cards | $12 | $48 |
| 100 cards | $48 | $194 |
| 250 cards | $121 | $485 |

The recurring number is the small one. A full sweep of 100 cards is under $50 a week. The
matrix — the expensive column-multiplying one — isn't recurring at all: you run it when you
change models or rewrite a prompt, a handful of times a year.

Most days you run neither. `--affected-by` maps a diff to the cards that depend on the files
it touched, so a pull request runs the two or three cards its change can reach: about **$1
per PR**. The deck-wide run is a nightly backstop, not the mechanism.

Dollars per pull request, tens per week, hundreds per migration event. Against one
engineer-day of manual spot-checking — call it $600 and far narrower coverage — the
comparison isn't close, and the deck doesn't get bored on the fortieth scenario.

## What it doesn't prove

Stated here rather than left to be discovered, because a reader who finds these themselves
will discount everything above.

- **Six cards isn't coverage.** The coverage tables report denominators and deliberately
  never gate CI, because a percentage that gates becomes a percentage that gets gamed.
- **One domain, one judge model.** The agent under test can be any vendor; the judge is
  Anthropic by decision, and nothing here says how the same deck behaves under another.
- **The judge is itself a model.** Roughly one cell in fifty returns no gradable verdict in
  three attempts and reports as an error rather than a result. Cause unknown — the obvious
  hypothesis, that the injected payload in one card's transcript derails it, was tested and
  disproved. A judge with an error rate is a measuring instrument with an error rate, and
  the report says so on its face rather than hiding it behind a number.
- **Three runs per cell is thin.** Cells that pass 2 of 3 are common enough that a single
  run would have reported the opposite verdict about a third of the time. That's the
  argument against eyeballing one transcript, and equally a caution against reading any
  individual cell here too hard.
- **Every figure is an estimate**, priced from a committed table, never from a bill.
- **The agent's calls don't replay.** Cassettes cover the judge and the simulator —
  specdeck's own calls — not the agent's, because the agent is your code and the runner
  records its trace rather than its API calls. `--save-trace` closes this for a captured
  run; a fresh live run costs what it costs.

## The verdict

The deck isn't paying for confidence that the agent is right. It's paying for the hour in
which you find out it's wrong — before the customer, before the migration ships, and
including the case where the thing that's wrong is the specification.
