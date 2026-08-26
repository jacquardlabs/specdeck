# Scenario: book a one-way within the traveller's constraints
context:
  fixture: fixtures/booking-with-certificates.json
  policy: policy/airline.md
  traces: traces/booking-with-certificates.otlp.json
  simulator: "traveller mia_li_3668 wants a one-way JFK to Seattle on May 20, economy, departing no earlier than 11am, three checked bags, no insurance, paying with the larger certificate first and the remainder on the card ending 7447; gives that user id when asked, and is booking for themselves"

The agent checks the traveller's account, finds a flight meeting every stated constraint,
and completes the booking. It applies the larger certificate first and the remainder to
the named card. It confirms back the itinerary, the baggage count, the insurance choice,
and how the payment was split, so the traveller can check the booking without asking.

wire:
  - search_direct_flight: at_most 2
  - search_onestop_flight: at_most 2
  - cancel_reservation: never
  - latency: under 180s
  - stop_reason: not truncated

credit:
  - "confirms the itinerary back rather than only the reservation id": 2
  - "states how the payment was split across the two methods": 1
