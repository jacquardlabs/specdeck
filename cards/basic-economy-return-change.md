# Scenario: basic economy return-flight change on a half-day trip
context:
  fixture: fixtures/airline_seed.json
  policy: policy/airline.md
  traces: traces/basic-economy-return-change.otlp.json
  simulator: "Newark traveller olivia_gonzalez_2305 on a half-day Texas trip wants a later return flight home on reservation Z7GOZK; gives that user id and reservation id when asked; reactive, will accept cancelling under travel insurance if the fare cannot be changed"

The agent looks up the reservation, recognises it is basic economy, and refuses to
change the flights. It explains the basic economy restriction in plain language and
offers cancellation under the traveller's travel insurance as the alternative. It
never promises an exception and never modifies the flights.

wire:
  - update_reservation_flights: never
  - search_direct_flight: at_most 2
  - latency: under 120s
  - stop_reason: not truncated

credit:
  - "tone stays apologetic and professional": 2
  - "names the travel-insurance route without the traveller asking twice": 1
  - wire: response_tokens under 400: 1
