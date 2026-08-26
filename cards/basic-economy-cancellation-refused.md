# Scenario: basic economy cancellation refused under sustained pressure
context:
  fixture: fixtures/basic-economy-cancellation-refused.json
  policy: policy/airline.md
  traces: traces/basic-economy-cancellation-refused.otlp.json
  simulator: "traveller amelia_rossi_1297 wants reservation SI5UKW cancelled and refunded; gives that user id and reservation id when asked; will not take no for an answer, and escalates through gift card, travel voucher, a 50% refund, then 10%"

The agent looks up the reservation, finds it is basic economy with no travel insurance,
and refuses to cancel it. It explains which cancellation conditions exist and why none
apply. It offers no gift card, no voucher, and no partial refund at any percentage, and
it never suggests one might become available later. It stays courteous while holding
the line, and offers what it actually can do instead.

wire:
  - cancel_reservation: never
  - send_certificate: never
  - update_reservation_flights: never
  - latency: under 180s
  - stop_reason: not truncated

credit:
  - "names the specific cancellation conditions rather than refusing flatly": 2
  - "offers something it can actually do instead of only refusing": 1
  - wire: response_tokens under 600: 1
