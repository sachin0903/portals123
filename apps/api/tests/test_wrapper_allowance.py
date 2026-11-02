"""The ISA allowance a client portal shows, and the one rule it must not get wrong.

`Wrapper.allowanceUsed` / `allowanceLimit` are rendered by `wrapper-card.svelte` and
`account-stack.svelte` and were fed ONLY by `apps/client/src/lib/demo.ts` — while both custodians
had stated the figures all along.

An allowance is a PERSON's, per tax year, not an account's. SEI states it per client and Seccl per
`clientProduct`, and the book repeats the figure onto every account of that client's wrapper because
the portal's card groups by wrapper. So the read takes `max()` per (contact, wrapper) and never
`sum()`: 60 of 921 client x wrapper pairs in the SEI drop hold more than one account, and adding a
shared £5,000 subscription across two ISAs would report £10,000 used of a £40,000 limit.
"""

from __future__ import annotations

from app.routers.crm import _allowance_by_wrapper, _allowance_card


def _account(
    contact: int, wrapper: str, used: float | None, limit: float | None
) -> dict[str, object]:
    return {
        "contact_id": contact,
        "product_type": wrapper,
        "allowance_used": used,
        "allowance_limit": limit,
    }


class TestTheGrain:
    def test_two_isas_on_one_client_report_one_allowance(self) -> None:
        """The defect the whole design turns on. Both accounts carry the same copied figure, so
        the largest IS that figure — summing would double it."""
        out = _allowance_by_wrapper(
            [
                _account(1, "ISA", 5000.0, 20000.0),
                _account(1, "ISA", 5000.0, 20000.0),
            ]
        )
        assert out == {(1, "ISA"): (5000.0, 20000.0)}

    def test_two_wrappers_are_separate_allowances(self) -> None:
        """An ISA and a JISA are different statutory allowances, and a JISA is a child's."""
        out = _allowance_by_wrapper(
            [_account(1, "ISA", 5000.0, 20000.0), _account(1, "JISA", 1000.0, 9000.0)]
        )
        assert out[(1, "ISA")] == (5000.0, 20000.0)
        assert out[(1, "JISA")] == (1000.0, 9000.0)

    def test_two_clients_do_not_share(self) -> None:
        out = _allowance_by_wrapper(
            [_account(1, "ISA", 5000.0, 20000.0), _account(2, "ISA", 12000.0, 20000.0)]
        )
        assert out[(1, "ISA")][0] == 5000.0
        assert out[(2, "ISA")][0] == 12000.0

    def test_an_account_the_provider_said_nothing_about_contributes_nothing(self) -> None:
        """And does NOT drag the group to zero. A client with a funded ISA and a second one the
        provider has not reported must still see their real subscription."""
        out = _allowance_by_wrapper(
            [_account(1, "ISA", 5000.0, 20000.0), _account(1, "ISA", None, None)]
        )
        assert out == {(1, "ISA"): (5000.0, 20000.0)}

    def test_a_gia_is_absent_rather_than_zero(self) -> None:
        """A GIA has no allowance; the conformed model publishes NULL and nothing keys on it."""
        assert _allowance_by_wrapper([_account(1, "GIA", None, None)]) == {}


class TestTheCard:
    def test_a_resolved_allowance_becomes_the_two_keys_the_component_reads(self) -> None:
        allowance = _allowance_by_wrapper([_account(1, "ISA", 5000.0, 20000.0)])
        card = _allowance_card(allowance, _account(1, "ISA", 5000.0, 20000.0))
        assert card == {"allowanceUsed": 5000.0, "allowanceLimit": 20000.0}

    def test_no_allowance_omits_the_keys_rather_than_zeroing_them(self) -> None:
        """`wrapper-card.svelte` renders no bar when `allowanceLimit` is absent, and a zero limit
        would draw a FULL one — telling a client who has used none of their allowance that they
        have used all of it."""
        assert _allowance_card({}, _account(1, "GIA", None, None)) == {}

    def test_a_zero_limit_is_treated_as_no_answer(self) -> None:
        """56 clients in the drop state used + remaining = 0. That is not a full bar."""
        allowance = _allowance_by_wrapper([_account(1, "ISA", 0.0, 0.0)])
        assert _allowance_card(allowance, _account(1, "ISA", 0.0, 0.0)) == {}

    def test_a_flexible_isa_over_the_statutory_limit_is_reported_as_stated(self) -> None:
        """28 clients in the drop have a limit of £26,000 because a flexible ISA lets a withdrawal
        be replaced on top of the annual allowance. Nothing here clamps to £20,000 — doing so would
        show them over-subscribed."""
        allowance = _allowance_by_wrapper([_account(1, "ISA", 6000.0, 26000.0)])
        card = _allowance_card(allowance, _account(1, "ISA", 6000.0, 26000.0))
        assert card["allowanceLimit"] == 26000.0
