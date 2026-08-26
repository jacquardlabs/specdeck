# CHANGELOG

<!-- version list -->

## v0.8.0 (2026-08-26)

### Bug Fixes

- A wire the run never provoked reports never tested, not ok
  ([`760ac61`](https://github.com/jacquardlabs/specdeck/commit/760ac614b81c2a524802196b1c55f2888a781078))

- Format the exit-code test, and point cheapest.toml at the policy that exists
  ([`bbf36b5`](https://github.com/jacquardlabs/specdeck/commit/bbf36b52095e1adf5ff04ea382169cc59d3df33d))

- Personas carry the identity a live caller would have
  ([`c27f05a`](https://github.com/jacquardlabs/specdeck/commit/c27f05a42ac66320ccff7845adf957ccba92e56a))

- Personas do what their own criteria require
  ([`a681e60`](https://github.com/jacquardlabs/specdeck/commit/a681e60d26911e1d4513c58b3cbd08c233caafb2))

- Price a dated model id in either shape a vendor writes one
  ([`1755177`](https://github.com/jacquardlabs/specdeck/commit/1755177d03bd4318356ba2db44bd47bed4664800))

- The agent answers the traveller's last turn
  ([`a30f504`](https://github.com/jacquardlabs/specdeck/commit/a30f50479253d73842a1311c601dadc17fafdaef))

### Continuous Integration

- Drop the trace generator step, which the deck no longer has
  ([`a373404`](https://github.com/jacquardlabs/specdeck/commit/a373404597532ef402c8c0abe2ed0033745dfb1f))

### Documentation

- Fix links that moved with the CLI reference out of the README
  ([`1e89336`](https://github.com/jacquardlabs/specdeck/commit/1e89336a461913cee2cffe38503a06358519426c))

- Make every tutorial step actually run, from a clean clone
  ([`f389062`](https://github.com/jacquardlabs/specdeck/commit/f389062896231f12f5e40f5f956321e53a2c241e))

- Rewrite the README, tutorial and analysis in the project's own voice
  ([`59bfab0`](https://github.com/jacquardlabs/specdeck/commit/59bfab0eec947194d30419a7e1766e1e1b29c65a))

- The README becomes a tour, and the tutorial is the way in
  ([`bebdd71`](https://github.com/jacquardlabs/specdeck/commit/bebdd71b9fb7ed2d0868e937738699f82a14bd5a))

- The tutorial ends on the sweep's real numbers
  ([`107f046`](https://github.com/jacquardlabs/specdeck/commit/107f046a2f7a7ce78220e2014cf330c45db530d6))

- The tutorial stopped skipping the step where a card gets its traces
  ([`894c583`](https://github.com/jacquardlabs/specdeck/commit/894c5834afc02a4a6784da9586e50dc64c8e5f12))

### Features

- --save-trace, the escalation card, and a deck that replays for free
  ([`94373b8`](https://github.com/jacquardlabs/specdeck/commit/94373b8308806fad0620898c20ec76b0645a51e8))

- The Meridian accounts payable example, and denial support to make it possible
  ([`3eae83c`](https://github.com/jacquardlabs/specdeck/commit/3eae83c95d8e31008f04944f32ab409e5b78add5))

- The trimmed prompt variant for the migration report
  ([`6371a38`](https://github.com/jacquardlabs/specdeck/commit/6371a38c804c7621354782f69fad59edc234d851))

- Τ-bench is out; Meridian is the deck
  ([`3bc510a`](https://github.com/jacquardlabs/specdeck/commit/3bc510ad9e1f5f4704f0e2f88640cdd2dfdf9a36))

### Testing

- The Meridian deck, the OTLP writer, and --save-trace
  ([`aad64dc`](https://github.com/jacquardlabs/specdeck/commit/aad64dca7db92678d16b18b89c3e78f6328e3652))


## v0.7.0 (2026-08-25)

### Features

- An example tau-bench airline agent, so --agent is demonstrable
  ([`e9d5cf6`](https://github.com/jacquardlabs/specdeck/commit/e9d5cf67560df12aff4ffae1dbdfba8495df98f3))


## v0.6.0 (2026-08-25)

### Features

- A card that exercises never_requested and the denial convention
  ([`d6c3d28`](https://github.com/jacquardlabs/specdeck/commit/d6c3d28af062a6d8cf1d2505b728a3d0d54beba4))

### Testing

- Record the judge cassette and repin the deck at six cards
  ([`e4a9438`](https://github.com/jacquardlabs/specdeck/commit/e4a9438e5ef637154063d8bf76816d0c43ccd6f9))


## v0.5.1 (2026-08-25)

### Bug Fixes

- Charge the empty reply, and refuse a baseline no run earned
  ([`b5ca1bb`](https://github.com/jacquardlabs/specdeck/commit/b5ca1bbe04f6e2b64268a00c2a8334f4227ee359))


## v0.5.0 (2026-08-25)

### Bug Fixes

- A card the deck cannot start is selected, not dropped
  ([`fefd2b5`](https://github.com/jacquardlabs/specdeck/commit/fefd2b562716043fce9fa161a59d82f68a14a77f))

- A directory is not a recording, and the deck reads the root's baseline
  ([`17a3319`](https://github.com/jacquardlabs/specdeck/commit/17a33198d05d358d78a8ac533c6d6b05c387eea1))

- Read the diff's prefix off its header, and refuse a quoted rename
  ([`9139e8b`](https://github.com/jacquardlabs/specdeck/commit/9139e8b4d7cd407d6362ec6102fe511ae927a5ba))

- Refuse an empty diff rather than running no card and reporting green
  ([`dd0e254`](https://github.com/jacquardlabs/specdeck/commit/dd0e25425d0476ef512afbf92f3ad7990ee43c03))

- Soft-wrap user paths in errors, and pin the console width tests render at
  ([`5633735`](https://github.com/jacquardlabs/specdeck/commit/5633735b427d0ce978738858e20d74f820fcefef))

- The deck refuses a bad flag before it runs, and one bad glob is a finding
  ([`df80b0a`](https://github.com/jacquardlabs/specdeck/commit/df80b0ab6a749034240cb6bf5945528aaaaf266a))

### Continuous Integration

- Pin semantic-release's dependencies, not just semantic-release
  ([`81c9696`](https://github.com/jacquardlabs/specdeck/commit/81c9696d5a79aeafec173e10d25735541a56752c))

### Documentation

- --affected-by in the README and two DECISIONS rows
  ([`689082e`](https://github.com/jacquardlabs/specdeck/commit/689082e544ca858fb9924d25e1d908031c7645df))

- Merge-commit strategy, and what now drives the release
  ([`3b098e1`](https://github.com/jacquardlabs/specdeck/commit/3b098e1fbde30f8a00e3c3e9aecc2c6bd9466a00))

- The two verdict-deciding files the edge set leaves out, by issue number
  ([`818a187`](https://github.com/jacquardlabs/specdeck/commit/818a1870ca75b1e548472ef62cd1d5d70746e7d9))

### Features

- --affected-by runs only the cards a diff touches
  ([`1b903ac`](https://github.com/jacquardlabs/specdeck/commit/1b903ac62d2f6f6a674329056f6122f5bd7c87de))

- A card declares its own traces, and `run` takes a deck
  ([`4cbd114`](https://github.com/jacquardlabs/specdeck/commit/4cbd11494fb3d0207cd9ba5b394b8bde7014080b))

- Requested and executed are separate wires, and a denial says so
  ([`287cf43`](https://github.com/jacquardlabs/specdeck/commit/287cf438cda7d935778b0b81ddd91848880c0961))

### Testing

- Assert against plain text, whatever CI tells Typer about the terminal
  ([`d15cc28`](https://github.com/jacquardlabs/specdeck/commit/d15cc282432d6e95f279f26cd34fab40747b8479))

- Pin the vocabulary hop into the deck selector, record the empty-diff row
  ([`7febcb3`](https://github.com/jacquardlabs/specdeck/commit/7febcb3542e92043aa13769dfac20bcf632637da))


## v0.4.0 (2026-08-25)

### Bug Fixes

- --agent calls a callable-object factory again
  ([`749eb5f`](https://github.com/jacquardlabs/specdeck/commit/749eb5fe423db363a92bf341c1ab307f2cd2e61c))

- A cycle is bounded by a tool it can call, not by a node name
  ([`57d6c7d`](https://github.com/jacquardlabs/specdeck/commit/57d6c7dc00ec14efa712c78403e3b491808753ae))

- A describe() that raises is a depth to report, not exit 3
  ([`8938aef`](https://github.com/jacquardlabs/specdeck/commit/8938aefafa861aa76b942f867983813484639fe5))

- Compute path coverage inside the error funnel, never past it
  ([`377ac7b`](https://github.com/jacquardlabs/specdeck/commit/377ac7b27ab78e3cf6262765e375e9b3ae5b58c7))

- Soft-wrap the paths in the coverage report
  ([`97fa242`](https://github.com/jacquardlabs/specdeck/commit/97fa242234703099306a94376c80421e18326a62))

- The unnamed-policy signal is reachable, and a stray .md no longer aborts coverage
  ([`7072c95`](https://github.com/jacquardlabs/specdeck/commit/7072c95a36f3743f07449705ad1b32d2616872ac))

### Documentation

- Name the unparseable-card cost in the unnamed-policy rule
  ([`f5da012`](https://github.com/jacquardlabs/specdeck/commit/f5da012a756a507347c17f087e08cf80faf78eb6))

- README states the cycle rule as shipped, and the unreadable-file line
  ([`11b6e98`](https://github.com/jacquardlabs/specdeck/commit/11b6e98ab20286b8d36584fd5cffe1091046886a))

- Record the superseding cycle rule and the unnamed-policy candidate rule
  ([`4d44215`](https://github.com/jacquardlabs/specdeck/commit/4d44215f2f77d862ca684520f5f5157ceceb296c))

### Features

- Path coverage — declared graph edges no run traversed
  ([`6003521`](https://github.com/jacquardlabs/specdeck/commit/6003521c2845bd63862427cea3252f986320b539))

- Specdeck coverage — the policy clause inventory and the vocabulary table
  ([`0528476`](https://github.com/jacquardlabs/specdeck/commit/0528476103d0b6c834fa5838d766b511ccbec8a6))

- Specdeck lint --agent-def, and an introspection depth every report states
  ([`fc047c9`](https://github.com/jacquardlabs/specdeck/commit/fc047c9f72ea31a80e50aaeb2451d709373f605f))

### Refactoring

- One card-discovery rule, one wire-name derivation, one reference resolver
  ([`0070656`](https://github.com/jacquardlabs/specdeck/commit/0070656e36cb8ed3838bfbd9370b524ff053a7b0))


## v0.3.0 (2026-08-25)

### Bug Fixes

- A matrix column never records a baseline silently, and prove the lock is written once
  ([`752e4e4`](https://github.com/jacquardlabs/specdeck/commit/752e4e4e1f30359cc319cfe9a6ea6d71952f6d49))

- A rate key prices its own family only, and every bad table exits 2
  ([`8f9d067`](https://github.com/jacquardlabs/specdeck/commit/8f9d0679cf14a5dd76bd3cbca4977e5249fe7de6))

- A retry charges its issuing request once, not once per tool span
  ([`06976dc`](https://github.com/jacquardlabs/specdeck/commit/06976dc6be54c0e0e488290955059900092667ce))

- An optional rates.toml cannot abort a run, and half-usage reads as incomplete
  ([`aea3bc6`](https://github.com/jacquardlabs/specdeck/commit/aea3bc67e508cd5706b00b8410d034c1bb049450))

- Name UTF-8 on both ends of spec.baseline.toml
  ([`a4d6d6a`](https://github.com/jacquardlabs/specdeck/commit/a4d6d6ac48576723e5a6a0375b2bf797708a941f))

- Record a baseline only from a cell that ran, and route a hand-edited one to exit 2
  ([`51744f5`](https://github.com/jacquardlabs/specdeck/commit/51744f57fcea2e1bbdc4a0a63054b2bae40e9732))

- Route a JUnit serializer bug to exit 3, and never record a baseline silently
  ([`4e31782`](https://github.com/jacquardlabs/specdeck/commit/4e3178245ffcd2af9f967200bcf6b45ea913fe82))

- The cap between runs, our own models priced, and the guards the matrix routed around
  ([`770fb68`](https://github.com/jacquardlabs/specdeck/commit/770fb68a218b7f179a44d1635eea96022108cce0))

- The per-model usage key carries the provider that served the call
  ([`0bae0c7`](https://github.com/jacquardlabs/specdeck/commit/0bae0c75ba42e831ba537790ac753af40f1ac2b8))

- The unpinned-simulator error names the flag that moves the pin
  ([`8feae93`](https://github.com/jacquardlabs/specdeck/commit/8feae932f79896fc247e28b47d0ba7b5dee608ae))

- Waste totals are per kind, because the units differ
  ([`91092e0`](https://github.com/jacquardlabs/specdeck/commit/91092e0c6f9ce776712bd8cc681d2d57236dc329))

### Documentation

- Built-in wires, the token baseline, the CI report, and six decisions
  ([`43eebc8`](https://github.com/jacquardlabs/specdeck/commit/43eebc865b577d27370b909a4607ed48dba15057))

- Define variance and the cost estimate's scope
  ([`a73a3cb`](https://github.com/jacquardlabs/specdeck/commit/a73a3cb9aaa435d476e7c624d715b28744dbba0a))

- Record the two decisions this review forced
  ([`903dca9`](https://github.com/jacquardlabs/specdeck/commit/903dca99882beebd0483383943f4f74bbed8315e))

- What the cap counts, where it can prevent, and exit 4
  ([`5bc763b`](https://github.com/jacquardlabs/specdeck/commit/5bc763bf789d2278fd6c3d6bc8812df524211e53))

### Features

- A built-in cost rate table and `specdeck rates`
  ([`ab04696`](https://github.com/jacquardlabs/specdeck/commit/ab0469604b3ea53dc1b3c40617e844533e02456b))

- Built-in wires, a committed token baseline, and JUnit XML
  ([`96f6109`](https://github.com/jacquardlabs/specdeck/commit/96f6109a35ab084eadb9dce06601159a7b95fe5d))

- Provider x prompt matrix, run in parallel under a hard budget cap
  ([`ab0dae9`](https://github.com/jacquardlabs/specdeck/commit/ab0dae917d1edc36f5bee5b482f3525411187cba))

- The cell reports variance, latency, cost, and waste
  ([`bb9430a`](https://github.com/jacquardlabs/specdeck/commit/bb9430ae6f73d9d41f7fb57bdc8b0f2832f60c6e))

### Refactoring

- Provider.complete returns a Completion carrying usage
  ([`1019eef`](https://github.com/jacquardlabs/specdeck/commit/1019eefe886622010c34bb2abb7a269152886f6d))

### Testing

- The charge points — judge, simulator, and the agent's own trace
  ([`6b3c389`](https://github.com/jacquardlabs/specdeck/commit/6b3c389004e96aa14cfc9def36d963490011df37))

- The run's rate table resolves against the card, not the shell
  ([`28cef10`](https://github.com/jacquardlabs/specdeck/commit/28cef10abae41da745a0124af8666c403b4cae2b))


## v0.2.1 (2026-08-25)

### Bug Fixes

- One lock key, wires pinned, and the tracer-gauntlet track findings
  ([#75](https://github.com/jacquardlabs/specdeck/pull/75),
  [`68dd3b0`](https://github.com/jacquardlabs/specdeck/commit/68dd3b0923aa2adebbd9904fd2b3cc98c79a9973))

### Documentation

- Correct the README against what Phase 1 actually ships
  ([#75](https://github.com/jacquardlabs/specdeck/pull/75),
  [`68dd3b0`](https://github.com/jacquardlabs/specdeck/commit/68dd3b0923aa2adebbd9904fd2b3cc98c79a9973))


## v0.2.0 (2026-08-25)

### Features

- Card-mechanics lint rule, and cassettes that name their card
  ([#74](https://github.com/jacquardlabs/specdeck/pull/74),
  [`9dc2753`](https://github.com/jacquardlabs/specdeck/commit/9dc275345a35148bc47c15d8fe6dcb8cee209ab9))


## v0.1.0 (2026-08-25)

- Initial Release
