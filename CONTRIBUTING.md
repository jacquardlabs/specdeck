# Contributing

## Repo settings

Matched to the sibling repos (cctx, gauntlet, talk-radio, serverless-rag).

| Setting | Value | Why |
|---|---|---|
| Merge method | squash only | One commit per PR on `main`; merge commits and rebase-merge are off. |
| Delete branch on merge | on | Branches are disposable; the PR is the record. |
| Linear history on `main` | required | |
| Force push to `main` | blocked | |
| Delete `main` | blocked | |
| Pull request required | yes, 0 approvals | A solo repo still gets the PR surface — diff, discussion, CI — without a second person to wait for. |
| Wiki | off | Docs live in the repo. |
| Issues | on | |

Enforced by the `main-branch-protection` ruleset, not classic branch protection.

**Status checks are not required yet, deliberately.** There is no CI, because there is no
code. When Phase 1 lands the parser and the linter, CI and required contexts land in the
same change. Two things to get right then:

- The required context strings must match the CI job names exactly. Renaming a job without
  updating the ruleset leaves a context that never reports and wedges every PR.
- A Python matrix leg is one required context each. Adding a version to the matrix means
  adding a context to the ruleset in the same change.

## Conventions

- **Conventional Commits** for commit subjects and PR titles. GitHub's squash-merge uses
  the PR title as the commit subject, and semantic-release parses it — a non-conforming
  title produces no release rather than an error, which is the quiet failure worth knowing
  about.
- **Small PRs, one per milestone.**
- **`uv` for all Python tooling.** Python 3.11+.
- **Ask before adding a dependency** beyond `httpx`, `pydantic`, `typer`, and `rich`.
- **Every decision gets a DECISIONS.md line** — date, decision, alternative rejected.
- **Real provider calls only behind `--live`.** The default mode replays cassettes.

## Where things live

| Path | What |
|---|---|
| `README.md` | What specdeck is, and the target surface |
| `PRODUCT.md` | Personas, principles, phases, non-goals, kill criteria |
| `DECISIONS.md` | Locked decisions and open spikes |
| `docs/card-format.md` | Card spec, wire semantics, lint rules |
| `docs/measurement.md` | Cells, coverage, mutation, calibration |
| `CLAUDE.md` | Operating instructions for agents working in this repo |
