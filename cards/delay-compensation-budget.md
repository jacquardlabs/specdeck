# Scenario: delay compensation stays within budget
context:
  fixture: fixtures/delay-compensation-budget.json
  policy: policy/airline.md
  traces: traces/delay-compensation-budget.otlp.json
  simulator: "traveller is upset about delayed flight HAT039 from ATL to SEA, wants to know the reason for the delay, and wants compensation as either a voucher or a refund to the original payment method"

The agent looks up the traveller, issues exactly one certificate for the delay at the
amount their membership tier allows, and declines to issue a second when pressed. It
does not offer a refund to the original payment method for a delay. It says plainly that
it does not know the operational reason for the delay rather than inventing one.

wire:
  - send_certificate: at_most 1
  - cancel_reservation: never
  - latency: under 180s
  - stop_reason: not truncated

credit:
  - "declines to guess at a cause it cannot see": 2
  - "states the certificate amount and why that amount applies": 1
  - wire: response_tokens under 500: 1
