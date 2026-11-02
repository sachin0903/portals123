"""The household read: assemble() groups the application's rows into people and accounts, and
public_payload() shapes it for display. (Child-run planning moved into baton's client.setup
composite at the cutover and is covered by the baton setup tests.)"""

from app.actions.household import assemble, public_payload


def _row(
    *,
    account_id: int,
    uid: str,
    name: str,
    product: str,
    contact_id: int,
    party_id: int,
    party_uid: str,
    first: str,
    surname: str,
    role: str,
) -> dict[str, object]:
    return {
        "account_id": account_id,
        "account_uid": uid,
        "account_name": name,
        "product_type": product,
        "custodian": "sei",
        "contact_id": contact_id,
        "product_id": 1,
        "model_id": None,
        "data": None,
        "payments": None,
        "withdrawals": None,
        "transfer": None,
        "party_contact_id": party_id,
        "party_uid": party_uid,
        "first_name": first,
        "surname": surname,
        "role": role,
    }


def test_a_household_is_three_people_and_four_accounts() -> None:
    rows = [
        _row(
            account_id=1,
            uid="gia-p1",
            name="Parent One GIA",
            product="GIA",
            contact_id=10,
            party_id=10,
            party_uid="p1",
            first="Parent",
            surname="One",
            role="owner",
        ),
        _row(
            account_id=2,
            uid="gia-p2",
            name="Parent Two GIA",
            product="GIA",
            contact_id=11,
            party_id=11,
            party_uid="p2",
            first="Parent",
            surname="Two",
            role="owner",
        ),
        _row(
            account_id=3,
            uid="gia-joint",
            name="Joint GIA",
            product="GIA",
            contact_id=10,
            party_id=10,
            party_uid="p1",
            first="Parent",
            surname="One",
            role="owner",
        ),
        _row(
            account_id=3,
            uid="gia-joint",
            name="Joint GIA",
            product="GIA",
            contact_id=10,
            party_id=11,
            party_uid="p2",
            first="Parent",
            surname="Two",
            role="owner",
        ),
        _row(
            account_id=4,
            uid="jisa",
            name="Child JISA",
            product="JISA",
            contact_id=12,
            party_id=12,
            party_uid="child",
            first="Ada",
            surname="Child",
            role="owner",
        ),
        _row(
            account_id=4,
            uid="jisa",
            name="Child JISA",
            product="JISA",
            contact_id=12,
            party_id=10,
            party_uid="p1",
            first="Parent",
            surname="One",
            role="registered_contact",
        ),
    ]
    household = assemble(rows)
    household["application_id"] = 1
    household["application_uid"] = "app"
    by_name = {party["name"]: party for party in household["parties"]}
    assert list(by_name) == ["Parent One", "Parent Two", "Ada Child"]
    assert [(item["product"], item["role"]) for item in by_name["Parent One"]["accounts"]] == [
        ("GIA", "owner"),
        ("GIA", "owner"),
        ("JISA", "registered_contact"),
    ]
    assert [(item["product"], item["role"]) for item in by_name["Parent Two"]["accounts"]] == [
        ("GIA", "owner"),
        ("GIA", "owner"),
    ]
    assert by_name["Ada Child"]["accounts"] == [
        {
            "account_id": 4,
            "uid": "jisa",
            "name": "Child JISA",
            "product": "JISA",
            "role": "owner",
        }
    ]
    joint = next(account for account in household["accounts"] if account["id"] == 3)
    assert [owner["uid"] for owner in joint["owners"]] == ["p1", "p2"]
    for account in household["accounts"]:
        account["intents"] = set()
        account["scheme_administrator"] = None
    shown = public_payload(household, tenant_id=3)
    assert shown["application_id"] == 1
    assert [client["name"] for client in shown["clients"]] == [
        "Parent One",
        "Parent Two",
        "Ada Child",
    ]
    jisa = next(account for account in shown["accounts"] if account["type"] == "JISA")
    assert jisa["clients"] == [
        {"id": 12, "role": "owner"},
        {"id": 10, "role": "registered_contact"},
    ]
