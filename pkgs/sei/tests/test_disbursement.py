"""The disbursement envelope. SEI converts the JSON to XML in key order, so the wrapper is
part of the contract and not just decoration."""

from sei.endpoints.cash import disbursement_body


def test_a_memo_disbursement_omits_the_rail_and_used_by() -> None:
    """Select's working withdrawal is a MemoDisbursement with no pay-to and no usedBy."""
    body = disbursement_body(
        3106,
        {"accountNumber": "MSP001062", "portfolioId": "59018837"},
        memo=True,
    )
    request = body["createCashDisbursement"]["CreateCashDisbursementRequest"]
    assert list(request) == ["MemoDisbursement", "firmId"]
    assert "CurrencyDisbursement" not in request
    assert "usedBy" not in request


def test_a_rail_disbursement_keeps_used_by_and_the_currency_block() -> None:
    body = disbursement_body(3106, {"accountNumber": "MSP001062"})
    request = body["createCashDisbursement"]["CreateCashDisbursementRequest"]
    assert list(request) == ["usedBy", "CurrencyDisbursement", "firmId"]
    assert request["usedBy"] == "Graphene"
