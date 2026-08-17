# specdeck

Card-based eval runner for LLM systems. Read [PRODUCT.md](PRODUCT.md) and the
[open milestones](https://github.com/jacquardlabs/specdeck/milestones) before proposing
anything; read [DECISIONS.md](DECISIONS.md) before proposing anything that contradicts it.

**Repo state: Phase 1, walking skeleton.** `src/specdeck/` runs one card against a
recorded trace: trace schema, property IR, lockfile, card parser, wires engine, trace
input, judge step, cell runner, report, CLI. Not yet built: running the agent, the
provider matrix, and lint.

## Non-negotiables

- **Do not improvise product decisions.** When the spec conflicts with something discovered
  in code, stop and ask.
- **The execution backend is our own loop** (DECISIONS.md, 2026-08-16). Inspect `.eval`
  logs are an accepted trace source — readable as zstd-compressed JSON, without the
  `inspect-ai` package. Reintroducing Inspect as a dependency needs a new decision entry.
- **Keep DECISIONS.md current.** Date, decision, alternative rejected — one line each. A
  decision that turns out wrong gets a new entry, never an edit.
- **Never auto-edit a user's card.** Drafting output is a PR.
- **Never style-police the SME prose zone.** Machine-verifiable violations are errors;
  anything about prose content is a suggestion.

## Build order

TDD the three shared artifacts first, because everything else reads them:

1. **Trace schema** — the OTel GenAI event log.
2. **Property IR** — the intermediate representation wires compile to:
   pattern × scope × event selector. Defined in [docs/card-format.md](docs/card-format.md).
3. **Lockfile** — `spec.lock.toml`.

Then: card parser → wires engine → judge step → single-cell report → lint.

Phases are [GitHub milestones](https://github.com/jacquardlabs/specdeck/milestones), each
carrying its exit artifact in the description. Do not start a phase before the prior
phase's artifact ships. Phases 3 and 4 end in kill-gate decision issues (#31, #35) — those
close with a recorded decision, either way, not by running out of features.

Work comes from issues. If you are about to do something with no issue, file one first.

## Tech stack

- Python 3.11+, `uv` for all tooling.
- Dependencies: `httpx`, `pydantic`, `typer`, `rich`. Anything beyond those needs an
  explicit ask. `inspect-ai` is not one of them: the backend spike closed against it.
- `ruff` for lint and format.
- MIT license.

## Testing

- Real provider calls only behind `--live`. The default mode replays cassettes.
- Cassettes are the substrate for the mutation runner in Phase 3 — treat them as fixtures
  with a second job, not as throwaway recordings.

## Conventions

- Conventional Commits for commit subjects and PR titles.
- Small PRs, one per milestone.
- Confirm the branch before committing. Never commit to `main`.
