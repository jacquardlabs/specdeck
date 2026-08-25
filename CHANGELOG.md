# CHANGELOG

<!-- version list -->

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
