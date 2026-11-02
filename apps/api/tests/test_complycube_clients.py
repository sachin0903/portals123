"""What a bound ComplyCube client is sent to bring it up to date (``clients.provider_changes``)."""

from __future__ import annotations

from app.complycube.clients import provider_changes

HELD = {
    "id": "cli_1",
    "type": "person",
    "externalId": "con_1",
    "email": "Ada@Example.com",
    "personDetails": {
        "firstName": "Ada",
        "lastName": "Lovelace",
        "dob": "1815-12-10",
        "gender": "female",
    },
}


def test_only_what_differs_is_sent_and_never_type_or_external_id() -> None:
    wanted = {
        "type": "person",
        "externalId": "con_1",
        "email": "ada@example.com",
        "personDetails": {
            "firstName": "Ada ",
            "lastName": "King",
            "dob": "1815-12-10",
            "nationality": "GB",
        },
    }
    assert provider_changes(HELD, wanted) == {
        "personDetails": {"lastName": "King", "nationality": "GB"}
    }


def test_nothing_is_sent_when_complycube_holds_the_same() -> None:
    wanted = {
        "type": "person",
        "externalId": "con_1",
        "email": "ada@example.com",
        "personDetails": {"firstName": "Ada", "lastName": "Lovelace", "dob": "1815-12-10"},
    }
    assert provider_changes(HELD, wanted) == {}


def test_a_value_we_do_not_hold_never_blanks_complycubes() -> None:
    wanted = {"type": "person", "email": " ", "personDetails": {"firstName": "", "dob": None}}
    assert provider_changes(HELD, wanted) == {}


def test_a_new_email_and_company_name_are_sent() -> None:
    held = {"type": "company", "email": "old@example.com", "companyDetails": {"name": "Old Ltd"}}
    wanted = {
        "type": "company",
        "email": " new@example.com ",
        "companyDetails": {"name": "Analytical Engines Ltd"},
    }
    assert provider_changes(held, wanted) == {
        "email": "new@example.com",
        "companyDetails": {"name": "Analytical Engines Ltd"},
    }


def test_metadata_goes_whole_with_ours_over_what_complycube_holds() -> None:
    held = {"metadata": {"ops_note": "kept", "graphene_firm": "Old name"}}
    wanted = {"metadata": {"graphene_firm": "New name", "graphene_origin": "portal"}}
    assert provider_changes(held, wanted) == {
        "metadata": {"ops_note": "kept", "graphene_firm": "New name", "graphene_origin": "portal"}
    }
    assert provider_changes({"metadata": wanted["metadata"]}, wanted) == {}


def test_a_clients_origin_is_never_changed() -> None:
    held = {"metadata": {"graphene_origin": "partner", "graphene_client_uid": "cl1"}}
    wanted = {"metadata": {"graphene_origin": "portal", "graphene_env": "staging"}}
    assert provider_changes(held, wanted) == {
        "metadata": {
            "graphene_origin": "partner",
            "graphene_client_uid": "cl1",
            "graphene_env": "staging",
        }
    }
    # Unset, it is set.
    assert provider_changes({}, wanted) == {"metadata": wanted["metadata"]}
