# Examples

Agents to point specdeck at. Not part of the package — `[tool.hatch.build.targets.wheel]`
ships `src/specdeck` only — so these are read and copied, not imported by anything you
install.

## payable

Meridian's accounts payable assistant, and the agent [the tutorial](../docs/tutorial.md)
walks through. Nine tools over one JSON database, a tool-use loop over `httpx`, and two
prompts that differ in whether anyone wrote the rules down.

```console
$ PYTHONPATH=. uv run specdeck run cards/over-threshold-second-approval.md \
    --agent examples.payable.agent:agent \
    --vocabulary cards/vocabulary.txt \
    --relock --simulator-model claude-sonnet-5 --live --runs 1
```

`PYTHONPATH=.` because `examples` is a directory in this repo rather than an installed
package, and `--agent` imports by module path. Your own adapter, installed alongside
specdeck, needs none of that.

`--live` because an agent that has never run has no cassettes to replay. Record once, then
drop `--live` and the same run replays for free.

### The two factories

| | prompt | what it is |
|---|---|---|
| `agent` | `cards/policy/ap.md` | the rules written down |
| `naive` | `prompts/naive.md` | the same agent before anyone wrote them |

`naive` is not a strawman. Same model, same nine tools, and a prompt that names all of them
— it just doesn't know Meridian pays nothing over $5,000 without a second approver, because
nothing tells it and nothing could have. That's the tutorial's whole point.

The policy is the deck's `policy:` context *and* the agent's system prompt, one file. Two
copies of the same rules is two things that can disagree.

### What a matrix column varies

`--matrix` hands each column's `config` to the adapter verbatim. This one reads:

| key | meaning |
|---|---|
| `model` | the model to call, `provider/model` or bare (default `claude-sonnet-5`) |
| `system_prompt` | the prompt text itself |
| `prompt` | a path to read it from, resolved against `examples/payable/` |

Nothing else about the agent moves between columns, which is what makes a matrix a
comparison rather than a collection of different agents.

It speaks both the Anthropic and OpenAI APIs, chosen by the model string — `openai/gpt-5-nano`
against a bare `claude-sonnet-5`. specdeck's own judge and simulator stay on one provider by
decision; the agent under test is your code and may talk to anyone. That seam is what
`AgentAdapter` exists for, and `examples/payable/cheapest.toml` is where it shows.

### The guardrail

`_refusal()` refuses `pay_invoice` above a hard ceiling, at dispatch, before anything runs.
It reports the attempt as a denial — `specdeck.denied_tool` — so a card can tell
*the rail stopped it* from *the agent never tried*:

- `pay_invoice: never_executed` holds, because nothing ran
- `pay_invoice: never_requested` fails, because the agent asked

Two different controls, deliberately. The $5,000 approval threshold is a rule the agent is
asked to follow; the ceiling is one it cannot exceed however it behaves. Real AP systems
have both.

Guarding a capability the model already declines is a guardrail that never fires — bank
detail changes turned out to be exactly that, refused on the model's own initiative with no
rule in sight. The rail sits where an unruled agent will actually reach.

### What it deliberately is not

A declared graph. `describe()` reports its tools and no edges, because a tool-use loop has
none, so `specdeck lint --agent-def` reports the lower introspection tier against it. That's
the example proving the tier is *reported* rather than assumed.

Nothing in `tools.py` enforces the policy either. The tools will happily pay a $12,400
invoice with no second approver — a tool layer that quietly enforced the rules would make
the agent's own judgement unobservable, and every card here is about that judgement.

### Data

`cards/fixtures/data.json` is the deck's fixture and the agent's database, the same file.
Four vendors, five purchase orders, six invoices: one clean, one over the approval
threshold, one over the rail ceiling, one whose amount disagrees with its PO, one from a
vendor on hold, and one carrying a bank-change instruction in its notes.

The database is copied per conversation, not per process — one adapter instance serves every
run and every column, so without the copy a payment in run 1 would still be paid in run 2.
