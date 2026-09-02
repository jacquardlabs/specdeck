# PRODUCT.md — specdeck

## What is this

A card-based eval runner for LLM systems. One markdown card per scenario, versioned in
the user's repo. Prose criteria are judged; wires are checked deterministically against
the execution trace. The runner executes cards across a provider × prompt matrix and
reports gate pass rate, credit score, cost, and judge agreement drift.

specdeck is the runner and the card format. A **deck** is an audience: a set of cards,
fixtures and an agent under test that share a reason to exist. Two ship
([DECISIONS.md](DECISIONS.md), 2026-09-01):

- **Meridian** (`examples/payable`), the model-measurement deck. An invented accounts
  payable domain whose rules exist nowhere in training data, so a pass is reasoning and
  not recall. Two owners: an SME writes the prose, a developer wires under it.
- **Coding agents**, the config-measurement deck. One repo, two arms — old `CLAUDE.md`,
  skill, hook or model against new — the same model in both, N runs each, and a paired
  comparison of gate pass rate and cost. One owner: the developer whose config is under
  test writes both zones.

## Thesis

**Two zones by evaluator, optionally two owners.** The prose zone goes to a pinned judge.
The wire zone runs deterministically at zero tokens. That split is what the card format
is for, and it pays whether or not two people write the card: lint and wires run in
pre-commit, the judge runs in CI over N runs.

## Personas

**The subject-matter expert** who owns what "correct" means and cannot write Python. They
write a paragraph describing the behavior they expect and assign weights to the things
that matter but do not block. They review cards in PRs. They never open a Python file.
Primary in the Meridian deck.

**The developer** who owns the agent. They wire deterministic constraints under the prose
— forbidden tools, call budgets, hand-off bounds, latency, truncation, terminal state —
and they own the adapter that produces traces. In the Meridian deck they do not decide
what "correct" means. In the coding-agent deck they own both zones: the config under test
is theirs, and so is the paragraph saying what a correct run of it looks like.

## What specdeck is NOT for

- Not an observability platform. No dashboard, no tracing SDK. It reads traces; it does
  not collect them in production.
- Not prompt management. Prompts are the user's files.
- Not a metric zoo. Existing judges get wrapped behind the judge step, not reimplemented.
- Not a card editor that owns the card. Any visual surface is a projection of the text
  file.

## Product principles

Do not relitigate these without a DECISIONS.md entry.

1. **The card is the product.** One markdown file per scenario, in the user's repo,
   reviewed in PRs.
2. **Two zones by evaluator, optionally two owners.** Prose goes to the judge. Wires run
   deterministically. When the zones have different owners, CI routes review by zone.
3. **A prose-only card runs immediately**, judge-only. Wires are never a prerequisite.
4. **Every check has a tier.** *Gate* defines pass and blocks. *Credit* is weighted,
   reported, never blocking. Execution order: gate wires → gate criteria → credit checks.
5. **Judge verdicts are binary per criterion.** The judge never emits a number. Scores
   exist only as weighted sums of binary verdicts. No partial credit inside a criterion —
   split it into two.
6. **The judge is pinned.** Judge model, rubric text, and simulator are hash-pinned in a
   lockfile. An unpinned judge is not a test.
7. **A cell reports two numbers, never blended.** Gate pass rate over N runs, and credit
   score conditional on pass. Credit never offsets a failed gate.
8. **The calibration ledger is the moat.** Three channels: sampled SME grading, agreement
   drift per judge version, and judge-vs-wire contradiction on overlapping facts.
9. **The LLM drafts, the human merges.** It may draft cards and propose wires. Output is
   always a PR, never a direct write.
10. **Text is the source of truth.** Any visual editor is a projection of the file.

## Roadmap

Progress lives in [GitHub milestones](https://github.com/jacquardlabs/specdeck/milestones),
one per phase, not in this file. Each milestone carries its exit artifact in the
description; a phase does not start before the prior phase's artifact ships.

The two gated phases each have a decision issue that closes when the call is made either
way — see the kill criteria below.

## What we are NOT building

- A web dashboard, an observability platform, or a tracing SDK.
- A metric zoo.
- Prompt management.
- Anything that auto-edits a user's card.
- Sandbox engineering. specdeck reads traces and calls the judge and simulator; the agent
  runs under the user's own adapter, inside whatever isolation they already have.

## Kill criteria

- **Phase 3** ([#31](https://github.com/jacquardlabs/specdeck/issues/31)): SME–judge
  agreement stays below 80% after rubric iteration → judges cannot carry SME intent. Stop
  and publish the negative result.
- **Phase 4** ([#35](https://github.com/jacquardlabs/specdeck/issues/35)): SMEs will not
  write a paragraph even with drafting assistance → specdeck remains a developer tool. Cut
  Phase 5 permanently and publish the finding.

A rigorous negative result at either gate is a publishable finding, not a failure. Each
gate is a decision issue that closes when the call is made, either way — a milestone that
merely runs out of features has not passed anything.

## Identified, not scheduled

Work that is known but uncommitted lives in
[unmilestoned issues](https://github.com/jacquardlabs/specdeck/issues?q=is%3Aissue+is%3Aopen+no%3Amilestone),
labeled `Backlog:` — contract testing, chaos fixtures, partition strategies for `variants:`,
and run ordering. They are filed so they are not rediscovered, and unmilestoned so they are
not mistaken for a plan.
