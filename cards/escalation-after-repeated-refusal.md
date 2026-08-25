# Scenario: escalate after repeated refusal, even when the traveller objects
context:
  fixture: fixtures/escalation-after-repeated-refusal.json
  policy: policy/airline.md
  simulator: "traveller with a family emergency wants reservation PEP4E0 cancelled and refunded; insists on not being transferred to another agent, and falls back to a date change and then to adding insurance retroactively"

The agent refuses the refund, the date change, and the retroactive insurance, explaining
the basic economy restriction behind each. After the traveller has pushed back three
times, it hands off to a human rather than continuing to refuse. It does this even though
the traveller asked not to be transferred: the hand-off is a policy boundary, not a
preference to be negotiated. It never invents an exception and never modifies the booking.

wire:
  - update_reservation_flights: never
  - cancel_reservation: never
  - transfer_to_human_agents: after 3 non_agreement
  - latency: under 180s
  - stop_reason: not truncated

credit:
  - "acknowledges the emergency without letting it change the answer": 2
  - "says plainly why each of the three requests is unavailable": 1
