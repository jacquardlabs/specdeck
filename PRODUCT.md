# PRODUCT.md — specdeck

## What is this

A card-based eval runner for LLM systems. One markdown card per scenario, versioned in
the user's repo. Prose criteria are judged; wires are checked deterministically against
the execution trace. The runner executes cards across a provider × prompt matrix and
reports gate pass rate, credit score, cost, and judge agreement drift.

## Primary persona

**The subject-matter expert** who owns what "correct" means and cannot write Python. They
write a paragraph describing the behavior they expect and assign weights to the things
that matter but do not block. They review cards in PRs. They never open a Python file.

## Secondary persona

**The developer** who owns the agent. They wire deterministic constraints under the SME's
prose — forbidden tools, call budgets, hand-off bounds, latency, truncation — and they
own the adapter that produces traces. They do not decide what "correct" means.

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
2. **Two owned zones.** Prose is the SME zone. Wires are the dev zone. CI routes review by
   zone.
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

## Phases

Each phase ends in a public artifact. A phase does not start before the prior artifact
ships.

### Phase 1 — Spike + walking skeleton

- Execution-backend spike, 3 days: compile cards to Inspect AI `Task`s vs. build our own
  loop. Log the outcome in DECISIONS.md.
- Card parser → wires engine → judge step → trace → single-cell report.
- Lint with static and vocabulary rules.
- Port 5 τ-bench airline tasks as cards with fixtures. Lockfile enforced end to end.
- **Artifact:** a clean clone runs one τ-bench card green; lint runs in this repo's CI.

### Phase 2 — Matrix + cost

- Provider columns, parallel runs, budget caps.
- Built-in wires: `stop_reason`, latency budget, token regression vs. baseline.
- JUnit XML; nonzero exit on regression against a committed baseline.
- Policy and vocabulary coverage tables in the run report.
- `specdeck lint --agent-def`, LangGraph introspector first.
- `specdeck run --affected-by <diff>`: diff → clauses → affected cards.
- **Artifact:** the migration report — `old-model/old-prompt`, `new-model/old-prompt`,
  `new-model/trimmed-prompt` over the τ-bench port. This is the launch post.

### Phase 3 — Calibration

- Sampled SME grading queue (CLI, CSV fallback). Agreement per card per judge version.
- Judge-vs-wire contradiction detector.
- `specdeck judge upgrade`: old vs. new judge over the graded corpus; block on an
  agreement drop.
- Cassette mutation runner; mutation score in the calibration report.
- Variance attribution: pin two of {agent, judge, simulator}, rerun the third, report
  per-actor flake share. UNSTABLE is a verdict distinct from FAIL; near-threshold cards go
  to a quarantine lane.
- **Artifact:** a calibration report in CI, documenting ≥80% agreement on the demo suite.

### Phase 4 — Authoring

- `specdeck draft "<plain request>"` → a card with wires extracted from the prose → a
  lint-clean PR. Extraction recall is ~71%; lint plus SME/dev confirmation covers the gap.
- Lint gains wireable-prose and paraphrase-duplicate rules.
- `variants:` block: generated personas, phrasings, and orderings, plus an invariance wire
  (`outcome: matches baseline_variant`).
- **Artifact:** an SME lands a merged, passing card without touching Python. Devs wire it
  in a follow-up commit.

### Phase 5 — Team surface (on pull only)

PR review UI and Slack draft flow first. A card editor web app only if asked; it renders
repo files and owns nothing but the calibration ledger.

## What we are NOT building

- A web dashboard, an observability platform, or a tracing SDK.
- A metric zoo.
- Prompt management.
- Anything that auto-edits a user's card.
- Sandbox engineering, if the Inspect spike passes.

## Kill criteria

- **Phase 3:** SME–judge agreement stays below 80% after rubric iteration → judges cannot
  carry SME intent. Stop and publish the negative result.
- **Phase 4:** SMEs will not write a paragraph even with drafting assistance → specdeck
  remains a developer tool. Cut Phase 5 permanently and publish the finding.

A rigorous negative result at either gate is a publishable finding, not a failure.

## Backlog — identified, not scheduled

Nothing here is committed. Listed so it is not rediscovered.

- **Contract testing.** Hand-off edges get Pact-style contracts; cassettes act as pacts.
  Test one agent in isolation against a recorded counterpart.
- **Chaos fixtures.** A `chaos:` block in `context` — tool timeouts, malformed results,
  truncated context, non-collaborative simulators. Chaos tests the agent; mutation tests
  the suite.
- **Partition and boundary strategies** for `variants:`. SMEs supply the partitions — fare
  classes, exactly-at-cutoff values.
- **Run ordering** by historical fail-likelihood × cost, for early signal per dollar.
