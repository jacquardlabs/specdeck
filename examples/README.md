# Examples

Agents to point specdeck at. Not part of the package — `[tool.hatch.build.targets.wheel]`
ships `src/specdeck` only — so these are read and copied, not imported by anything you
install.

## airline

The τ-bench airline domain as one `AgentAdapter`: the fourteen tools ported from upstream,
a tool-use loop over `httpx`, and the policy as its system prompt. It is what the README's
`yourpkg.adapter:Agent` stands in for, and what the cards under `cards/` were ported from.

```console
$ PYTHONPATH=. specdeck run cards/basic-economy-cancellation-refused.md \
    --agent examples.airline.agent:agent \
    --relock --simulator-model claude-sonnet-5 \
    --live --runs 1
```

`PYTHONPATH=.` because `examples` is a directory in this repo rather than an installed
package, and `--agent` imports by module path. Your own adapter, installed alongside
specdeck, needs no such thing.

`--live` because an agent that has never run has no cassettes to replay. Record once, then
drop `--live` and the same run replays for free.

### What a column varies

`--matrix` hands each column's `config` to the adapter verbatim. This one reads two keys:

| key | meaning |
|---|---|
| `model` | the model this column runs (default `claude-opus-5`) |
| `system_prompt` | the prompt text itself |
| `prompt` | a path to read it from, resolved against `examples/airline/` |

Nothing else about the agent moves between columns, which is what makes a matrix a
comparison rather than a collection of different agents.

### What it deliberately is not

A declared graph. `describe()` reports its tools and no edges, because a tool-use loop has
none — so `specdeck lint --agent-def` reports the lower introspection tier against it. That
is the example proving the tier is *reported* rather than assumed, which is the failure
`#21` was written to prevent.

### Data

`data.json` is upstream's flight table entire, plus the reservations and users the
committed cards name. The flights are not sliced on purpose: a trimmed flight table makes a
live agent's search return nothing, and then a run grades the data rather than the model.

The database is copied per conversation, not per process — one adapter instance serves
every run and every column of an invocation, so without the copy a cancellation in run 1
would still be cancelled in run 2.

See `NOTICE` for what is derived from τ-bench and under what licence.
