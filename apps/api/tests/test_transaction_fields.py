"""The sterling line under a foreign amount is the custodian's rate, applied to the figure shown."""

from app.transaction_fields import base_amount, present_transaction, public_record, unit_price

EUR_RECEIPT = {
    "base_currency": "GBP",
    "local_net_transaction_amount": "1000000000",
    "base_net_transaction_amount": "857000000",
    "local_transaction_gross_amount": "1000000000",
    "transaction_description": "Cash Receipt",
}

USD_RECEIPT = {
    "base_currency": "GBP",
    "local_net_transaction_amount": "1000000000",
    "base_net_transaction_amount": "735916400",
}

# A buy whose face is the consideration and whose net includes charges. The sterling line
# has to follow the face, not replace it with the larger base net.
ACCENTURE = {
    "base_currency": "GBP",
    "local_transaction_gross_amount": "-17300",
    "local_net_transaction_amount": "-68424.37",
    "base_net_transaction_amount": "-50354.61",
    "transaction_description": "Buy Accenture",
}

FILE_USD = {
    "ACCOUNT_BASE_CURRENCY": "GBP",
    "TRANSACTION_CURRENCY": "USD",
    "NET_TRANSACTION_AMOUNT": "10000000",
    "BASE_TRANSACTION_AMOUNT": "7550018.90",
    "BASE_EXCHANGE_RATE": "0.7550018900",
    "_s_net": "1",
    "_s_base": "1",
    "DEBIT_CREDIT_FLAG_1": "C",
    "NARRATIVE": "Cash Receipt",
    "COMMISSION": "0.0000",
    "REVERSAL_FLAG": False,
}


def test_a_euro_receipt_converts_at_the_custodians_rate() -> None:
    assert base_amount(1_000_000_000, "EUR", "GBP", EUR_RECEIPT) == 857_000_000


def test_a_dollar_receipt_converts_at_the_custodians_rate() -> None:
    assert base_amount(1_000_000_000, "USD", "GBP", USD_RECEIPT) == 735_916_400


def test_a_buy_scales_the_figure_on_the_row() -> None:
    """£50,354.61 is the base net, which includes charges. The row shows −$17,300."""
    converted = base_amount(-17_300, "USD", "GBP", ACCENTURE)
    assert converted == -12_731.35
    assert converted != -50_354.61


def test_a_file_row_uses_the_signed_base_over_the_signed_net() -> None:
    assert base_amount(10_000_000, "USD", "GBP", FILE_USD) == 7_550_018.90


def test_the_same_currency_needs_no_second_line() -> None:
    assert base_amount(63_100_000, "GBP", "GBP", {"base_currency": "GBP"}) is None


def test_a_zero_amount_needs_no_second_line() -> None:
    assert base_amount(0, "USD", "GBP", {"BASE_EXCHANGE_RATE": "1"}) is None


def test_no_rate_means_no_line() -> None:
    assert base_amount(100, "USD", "GBP", {"transaction_description": "Cash"}) is None


def test_the_row_carries_the_line_and_drops_the_raw_payload() -> None:
    shown = present_transaction(
        {
            "date": "2026-08-27",
            "amount": 1_000_000_000,
            "currency": "EUR",
            "base_currency": "GBP",
            "payload": EUR_RECEIPT,
        }
    )
    assert shown["date"] == "2026-08-27"
    assert shown["base_amount"] == 857_000_000
    assert shown["base_currency"] == "GBP"
    assert "payload" not in shown
    assert shown["record"]["transaction_description"] == "Cash Receipt"


def test_the_account_currency_fills_in_when_the_column_is_blank() -> None:
    shown = present_transaction(
        {"amount": 10_000_000, "currency": "USD", "base_currency": None, "payload": FILE_USD}
    )
    assert shown["base_currency"] == "GBP"
    assert shown["base_amount"] == 7_550_018.90


def test_the_record_keeps_facts_and_drops_the_bookkeeping() -> None:
    record = public_record(FILE_USD)
    assert record["NARRATIVE"] == "Cash Receipt"
    assert record["BASE_EXCHANGE_RATE"] == "0.7550018900"
    assert "COMMISSION" not in record
    assert "REVERSAL_FLAG" not in record
    assert "_s_net" not in record
    assert "DEBIT_CREDIT_FLAG_1" not in record


def test_an_api_order_prices_the_consideration_over_the_units() -> None:
    """The narrative says "@ 5.1832 GBP". The API sends no unit price of its own."""
    assert (
        unit_price(
            {
                "local_transaction_gross_amount": "-12439.6800",
                "transaction_quantity": "2400",
            }
        )
        == 5.1832
    )


def test_a_file_row_keeps_the_quoted_price_and_the_signed_fee() -> None:
    shown = present_transaction(
        {
            "amount": -73171.22,
            "currency": "GBP",
            "quantity": 15770,
            "price": None,
            "fee_amount": None,
            "tax_amount": None,
            "payload": {
                "UNIT_PRICE": "4.6399000000",
                "NET_FEE_AMT": "476.6200000000",
                "_s_fee": "-1",
                "NET_TAXES_AMT": "0.0000000000",
                "_s_tax": "1",
            },
        }
    )
    assert shown["price"] == 4.6399
    assert shown["fee_amount"] == -476.62
    assert shown["tax_amount"] is None


def test_a_zero_column_falls_through_to_the_payload() -> None:
    shown = present_transaction(
        {
            "amount": -10,
            "currency": "GBP",
            "price": "42.00000000",
            "fee_amount": "0.0000",
            "tax_amount": None,
            "payload": {"net_charges_amount": "-9.99", "net_taxes_amount": "-1.50"},
        }
    )
    assert shown["price"] == 42
    assert shown["fee_amount"] == -9.99
    assert shown["tax_amount"] == -1.5


def test_a_true_flag_is_kept() -> None:
    assert public_record({"REVERSAL_FLAG": True}) == {"REVERSAL_FLAG": "Yes"}
