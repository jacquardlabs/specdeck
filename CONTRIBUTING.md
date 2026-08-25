# Contributing

## Repo settings

Matched to the sibling repos (cctx, gauntlet, talk-radio, serverless-rag).

| Setting | Value | Why |
|---|---|---|
| Merge method | merge commit only | Squash and rebase-merge are off. Both rewrite the parent's commits, so every child of a stacked PR conflicts; a merge commit lands the parent's commits on `main` unchanged, and the child then carries only its own. |
| Delete branch on merge | on | Branches are disposable; the PR is the record. |
| Linear history on `main` | not required | A merge-commit strategy and required-linear-history are mutually exclusive; the merge commits are the deliberate choice. |
| Force push to `main` | blocked | |
| Delete `main` | blocked | |
| Pull request required | yes, 0 approvals | A solo repo still gets the PR surface — diff, discussion, CI — without a second person to wait for. |
| Wiki | off | Docs live in the repo. |
| Issues | on | |
| Required status checks | `lint`, `test (3.11)`, `test (3.12)` | Bound to GitHub Actions by integration id, so nothing else can report a context under those names. |
| Up-to-date branch required | no | Requiring it forces a rebase every time `main` moves, which is friction a solo repo with linear history does not need. |

Enforced by the `main-branch-protection` ruleset, not classic branch protection.

**Changing a CI job name is a two-file change.** The required context strings must match
the job names exactly; renaming a job without updating the ruleset leaves a context that
never reports and wedges every PR, with no error to read. A Python matrix leg is one
context each, so adding a version means adding a context in the same change.

Verify a ruleset edit against a real PR rather than by reading it back: a wrong context
string still saves cleanly, and only shows up as `mergeStateStatus: BLOCKED` on a PR whose
checks have all passed.

```console
$ gh pr view <n> --json mergeable,mergeStateStatus
{"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN"}
```

## Issues and milestones

Progress is tracked in issues, not in docs. One milestone per phase, each carrying its exit
artifact in the description.

- **Every PR closes an issue.** `Closes #N` in the body. File one first if none exists.
- **Milestone = phase.** An issue with no milestone is identified but not scheduled, and
  that is a deliberate state — do not milestone it to make a board look complete.
- **Kill gates are issues** (#31, #35). They close when the decision is recorded, either
  way. A milestone that runs out of features has not passed its gate.
- **Deferred findings get an issue before the PR opens**, not after.

## Conventions

- **Conventional Commits** for commit subjects and PR titles. Under merge commits the
  subject of every commit on the branch is load-bearing, not just the PR title:
  python-semantic-release defaults `ignore_merge_commits = True`, so it skips the merge
  commit itself and parses the individual commits behind it. A PR whose commits are all
  non-conforming produces no release rather than an error, which is the quiet failure worth
  knowing about. The PR title still matters for review and for the changelog a reader
  scans, but it no longer drives the version bump.
- **Small PRs, one per milestone.**
- **`uv` for all Python tooling.** The floor is 3.11 and the CI matrix will test it;
  `.python-version` pins 3.12 as the local dev target, matching the sibling repos. The two
  numbers differ on purpose.
- **Ask before adding a dependency** beyond `httpx`, `pydantic`, `typer`, and `rich`.
- **Every decision gets a DECISIONS.md line** — date, decision, alternative rejected.
- **Real provider calls only behind `--live`.** The default mode replays cassettes.

## Where things live

| Path | What |
|---|---|
| `README.md` | What specdeck is, and the target surface |
| `PRODUCT.md` | Personas, principles, non-goals, kill criteria — no roadmap; that is in milestones |
| `DECISIONS.md` | Locked decisions and open spikes |
| `docs/card-format.md` | Card spec, wire semantics, lint rules |
| `docs/measurement.md` | Cells, coverage, mutation, calibration |
| `CLAUDE.md` | Operating instructions for agents working in this repo |
