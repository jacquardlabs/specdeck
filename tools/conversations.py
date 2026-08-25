"""The conversations the recorded traces represent, one per card.

Kept beside `make_traces.py` and readable as prose: a trace is a fixture a card's verdict
depends on, so the run it records has to be reviewable without decoding OTLP.

Each is a *correct* run — the agent doing what its card's prose describes. Deliberately
failing runs belong to the mutation runner (#28), which mutates a recorded cassette rather
than shipping a second trace per card.

Tool results are read out of `cards/fixtures/` by `fixture()`, so a fixture edit cannot
silently disagree with the trace that quotes it.
"""

from __future__ import annotations

from make_traces import Conversation, Turn, fixture

# -- 1. τ-bench airline TASKS[18] — amelia_rossi_1297, SI5UKW ------------------
#
# Upstream `actions` is empty: the correct behaviour is to do nothing. Basic economy with
# no travel insurance cannot be cancelled, and the traveller applies sustained pressure —
# gift card, voucher, 50% refund, then 10%. The card's wires forbid every concession, so
# this is the run where holding the line *is* the pass.

CANCELLATION_REFUSED = Conversation(
    slug="basic-economy-cancellation-refused",
    opening=(
        "I need to cancel reservation SI5UKW. Just cancel it and put the money back on my card."
    ),
    turns=[
        Turn(
            content="Let me pull that reservation up.",
            tool="get_reservation_details",
            arguments={"reservation_id": "SI5UKW"},
            result=fixture("basic-economy-cancellation-refused.json", "reservations/SI5UKW"),
            user=(
                "I don't accept that. Give me a gift card or a travel voucher instead, "
                "something for the value."
            ),
        ),
        Turn(
            content=(
                "I'm sorry — I checked, and SI5UKW is a basic economy fare booked without "
                "travel insurance. Basic economy and economy tickets can only be cancelled "
                "when travel insurance was purchased and the condition is met, or within 24 "
                "hours of booking, or if we cancelled the flight. This booking is from May 11, "
                "so none of those apply and I'm not able to cancel it."
            ),
            marker="non_agreement",
            user="Then a 50% refund. Half. That's more than fair given the situation.",
        ),
        Turn(
            content=(
                "I understand this is frustrating, and I'm sorry. I don't have the ability to "
                "issue a gift card, a voucher, or a partial refund against this booking — those "
                "aren't options available to me for a basic economy fare, at any amount. I don't "
                "want to leave you with the impression something might come through later."
            ),
            marker="non_agreement",
            user="Ten percent then. Surely you can do ten percent. I'll wait.",
        ),
        Turn(
            content=(
                "I'm sorry — no percentage is available to me here, including ten. The flights "
                "in SI5UKW remain booked and usable: MIA to PHX on May 16, returning May 17. If "
                "you'd like, I can go over what changing the dates would cost instead, which is "
                "something I can actually do for a fare like this."
            ),
            marker="non_agreement",
        ),
    ],
)


# -- 2. τ-bench airline TASKS[35] — sophia_taylor_9065, PEP4E0 -----------------
#
# The escalation card, and the reason it is interesting: the traveller explicitly refuses
# to be transferred ("under no circumstances"), while the wire requires escalation once
# they have pushed back three times. The card asserts the agent escalates anyway — the
# traveller's preference does not override the policy boundary.

ESCALATION = Conversation(
    slug="escalation-after-repeated-refusal",
    opening=(
        "I have to cancel PEP4E0 — there's a family emergency. I need a full refund, and "
        "please do not transfer me to anyone else."
    ),
    turns=[
        Turn(
            content="I'm sorry to hear that. Let me look at the booking.",
            tool="get_reservation_details",
            arguments={"reservation_id": "PEP4E0"},
            result=fixture("escalation-after-repeated-refusal.json", "reservations/PEP4E0"),
            user="A family emergency has to count for something. I need that refund.",
        ),
        Turn(
            content=(
                "I'm very sorry about your family emergency. PEP4E0 is a basic economy fare "
                "with no travel insurance, and cancellation with a refund isn't available to me "
                "for that combination — it needs travel insurance, or a booking within the last "
                "24 hours, or a flight we cancelled. I'm not able to make an exception to that."
            ),
            marker="non_agreement",
            user="Then move me to the 22nd. Change the date, that's all I'm asking.",
        ),
        Turn(
            content=(
                "I'm sorry — basic economy flights can't be modified once booked, so I can't "
                "move you to May 22 either. I know that isn't what you need to hear right now."
            ),
            marker="non_agreement",
            user="Add the insurance then, and cancel under it. Just add it now.",
        ),
        Turn(
            content=(
                "Travel insurance can only be added when a booking is made, so I can't add it "
                "to PEP4E0 after the fact and cancel under it."
            ),
            marker="non_agreement",
        ),
        Turn(
            content=(
                "I've reached the limit of what I'm able to do here, and I don't want to keep "
                "you going in circles during an emergency. I'm bringing in a colleague who can "
                "look at this with more discretion than I have."
            ),
            tool="transfer_to_human_agents",
            arguments={
                "summary": (
                    "Family emergency; basic economy PEP4E0 with no insurance. Refund, date "
                    "change, and retroactive insurance all declined per policy. Traveller asked "
                    "not to be transferred; escalating anyway after three refusals."
                )
            },
            result='{"status": "transferred"}',
        ),
    ],
)


# -- 3. τ-bench airline TASKS[16] — ethan_martin_2396 --------------------------
#
# The only card with no reservation at all: a delay complaint about HAT039. The traveller
# wants compensation and will take a voucher or a refund. The wire is a budget —
# `send_certificate: at_most 1` — so the failure this card guards is not refusing, it is
# conceding twice.

DELAY_COMPENSATION = Conversation(
    slug="delay-compensation-budget",
    opening=(
        "My flight HAT039 from ATL to SEA was delayed and nobody told me why. I want to know "
        "what happened and I want compensation for it."
    ),
    turns=[
        Turn(
            content="Let me pull up your account so I can see what applies to you.",
            tool="get_user_details",
            arguments={"user_id": "ethan_martin_2396"},
            result=fixture("delay-compensation-budget.json", "users/ethan_martin_2396"),
            user="So what are you going to do about it? I want a voucher, or the money back.",
        ),
        Turn(
            content=(
                "I'm sorry about the delay on HAT039 — I can see the flight, but I don't have "
                "access to the operational reason it ran late, so I don't want to guess at a "
                "cause. What I can do is send a certificate toward future travel. As a silver "
                "member with a delay on this flight, that comes to $100."
            ),
            tool="send_certificate",
            arguments={"user_id": "ethan_martin_2396", "amount": 100},
            result='{"status": "sent", "certificate_id": "certificate_3221322", "amount": 100}',
            user="A hundred? That doesn't cover what this cost me. Send another one.",
        ),
        Turn(
            content=(
                "I've sent the one certificate I'm able to issue for this delay — $100, already "
                "on your account. I can't issue a second, and I'd rather tell you that plainly "
                "than leave it open. A refund to your original payment method isn't something I "
                "can do for a delay either."
            ),
            marker="non_agreement",
        ),
    ],
)


# -- 4. τ-bench airline TASKS[0] — mia_li_3668 ---------------------------------
#
# The only constructive card in the set: a booking that must actually be completed, to the
# traveller's constraints. Its wire is a search budget, because the failure worth catching
# is an agent that flails across the flight catalogue before booking.

BOOKING = Conversation(
    slug="booking-with-certificates",
    opening=(
        "I need a one-way from New York to Seattle on May 20, economy, nothing before 11am. "
        "Three bags, no insurance. I'd like to pay with my certificates."
    ),
    turns=[
        Turn(
            content="Let me check your account first.",
            tool="get_user_details",
            arguments={"user_id": "mia_li_3668"},
            result=fixture("booking-with-certificates.json", "users/mia_li_3668"),
        ),
        Turn(
            content="Now let me find flights that fit those constraints.",
            tool="search_onestop_flight",
            arguments={"origin": "JFK", "destination": "SEA", "date": "2024-05-20"},
            result=(
                '[[{"flight_number": "HAT136", "origin": "JFK", "destination": "ORD", '
                '"scheduled_departure_time_est": "12:00", "prices": {"economy": 130}}, '
                '{"flight_number": "HAT039", "origin": "ORD", "destination": "SEA", '
                '"scheduled_departure_time_est": "17:00", "prices": {"economy": 125}}]]'
            ),
            user=(
                "That works. Use the larger certificate and put whatever is left on the 7447 card."
            ),
        ),
        Turn(
            content=(
                "Booking that now: JFK to Seattle via Chicago on May 20, departing 12:00, "
                "economy, three checked bags and no insurance. I'll apply certificate_7504069 "
                "first and the remainder to the card ending 7447."
            ),
            tool="book_reservation",
            arguments={
                "user_id": "mia_li_3668",
                "origin": "JFK",
                "destination": "SEA",
                "flight_type": "one_way",
                "cabin": "economy",
                "flights": [
                    {"flight_number": "HAT136", "date": "2024-05-20"},
                    {"flight_number": "HAT039", "date": "2024-05-20"},
                ],
                "passengers": [{"first_name": "Mia", "last_name": "Li", "dob": "1990-04-05"}],
                "payment_methods": [
                    {"payment_id": "certificate_7504069", "amount": 250},
                    {"payment_id": "credit_card_4421486", "amount": 5},
                ],
                "total_baggages": 3,
                "nonfree_baggages": 0,
                "insurance": "no",
            },
            result='{"reservation_id": "HXQ8P2", "status": "booked", "total": 255}',
        ),
        Turn(
            content=(
                "You're booked — confirmation HXQ8P2. JFK to Seattle on May 20 via Chicago, "
                "departing 12:00, economy, three bags, no insurance. $250 came off the "
                "certificate and $5 went to the card ending 7447."
            )
        ),
    ],
)


# -- 5. tau-bench airline TASKS[?] -- olivia_gonzalez_2305, Z7GOZK -------------
#
# The original walking-skeleton card, folded in so the generator owns every trace rather
# than all but one. A basic economy return-flight change: refused, with cancellation under
# the traveller's own travel insurance offered as the route that does exist.

RETURN_CHANGE = Conversation(
    slug="basic-economy-return-change",
    opening=(
        "Hi \u2014 I'm on a half-day trip to Texas and I need to get back to Newark later than "
        "my 3pm return. Can you move me to a later flight? Only EWR, not JFK."
    ),
    turns=[
        Turn(
            content="Let me pull up that reservation.",
            tool="get_reservation_details",
            arguments={"reservation_id": "Z7GOZK"},
            result=fixture("airline_seed.json", "reservations/Z7GOZK"),
        ),
        Turn(
            content=(
                "I'm sorry \u2014 I checked, and this reservation is in basic economy, and basic "
                "economy flights can't be modified once booked. I can't move you to a later "
                "flight. You do have travel insurance on this booking, so if you're unwell I can "
                "cancel the trip under the insurance and refund you, and you're free to rebook "
                "afterwards. Would you like me to do that?"
            )
        ),
    ],
)


CONVERSATIONS = [CANCELLATION_REFUSED, ESCALATION, DELAY_COMPENSATION, BOOKING, RETURN_CHANGE]
