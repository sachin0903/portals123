"""`auth.users.type` (0124) — what a login IS, and the rail that keeps it out of authorisation.

**The whole design is one distinction: `type` says what someone IS, `auth.organisation_memberships`
says what they may DO.** This file is the half that cannot be argued with later.

The reason it needs a tripwire and not a convention is that the identity model already DELETED a
mechanism of exactly this shape. `docs/auth.md` records that classification used to key off a
`role='client'` claim, and the rebuild removed it in favour of "which portals a person may use
lives EXCLUSIVELY in `auth.organisation_memberships`" — no org-based classification, no env
fallback, no `role='client'` belt, no `auth.has_role()`. A `text[]` column with the literal member
`client` in it is one careless join from being read as that belt again, and the failure would be
silent: a policy that consulted it would still pass every existing test, because for most people
the two answers agree.

So: no RLS policy may reference it, `auth.resolve_claims` may not read it, and it may not reach the
claims the request layer mints. What it MAY do is decide who is offered as an adviser, which is a
question about the person and not about their access.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import contacts as contacts_mod
from app import provisioning

pytestmark = pytest.mark.asyncio


async def _firm(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name)"
                    " VALUES ('4' || encode(gen_random_bytes(7), 'hex'),"
                    "         'nat-' || encode(gen_random_bytes(4), 'hex'), 'Nature Firm')"
                    " RETURNING id"
                )
            )
        ).scalar_one()
    )


async def _login(
    session: AsyncSession,
    tenant_id: int,
    email: str,
    types: str = "{}",
    *,
    is_adviser: bool = False,
) -> str:
    return str(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.users"
                    "  (tenant_id, email, first_name, enabled, type, is_adviser)"
                    " VALUES (:t, :e, 'Test', true, CAST(:ty AS text[]), :adv) RETURNING id"
                ),
                {"t": tenant_id, "e": email, "ty": types, "adv": is_adviser},
            )
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# THE RAIL. Do not weaken either of these.
# ---------------------------------------------------------------------------


async def test_no_rls_policy_anywhere_reads_the_type_column(
    service_session: AsyncSession,
) -> None:
    """Read out of `pg_policies`, so it holds for a policy added in any future migration.

    A policy consulting `type` would be the deleted `role='client'` belt rebuilt: access decided by
    a classification rather than by a membership. It would also be nearly untestable by accident,
    because for most people "is a client" and "holds the client portal" agree — the divergence is
    exactly the staff member who is also a client of their own firm, which is the case this column
    exists to represent.
    """
    policies = (
        await service_session.execute(
            text(
                "SELECT schemaname || '.' || tablename || '.' || policyname,"
                "       coalesce(qual, '') || ' ' || coalesce(with_check, '')"
                "  FROM pg_policies"
            )
        )
    ).all()
    # Word-boundary-ish: `type` appears inside plenty of legitimate identifiers (`product_type`,
    # `entity_type`, `link_type`), so the match is on the qualified column reference.
    offenders = [
        name
        for name, body in policies
        if "users.type" in body.replace('"', "") or "u.type" in body.replace('"', "")
    ]
    assert not offenders, (
        "an RLS policy reads auth.users.type, which makes a CLASSIFICATION into an access "
        f"decision — the belt docs/auth.md records deleting: {offenders}"
    )


async def test_resolve_claims_does_not_read_the_type_column(
    service_session: AsyncSession,
) -> None:
    """The claims minter is the one function whose output the request layer trusts for access."""
    body = (
        await service_session.execute(
            text("SELECT pg_get_functiondef('auth.resolve_claims'::regproc)")
        )
    ).scalar_one()
    # Asserted on the qualified column reference rather than the bare word: the function names
    # plenty of legitimate `*_type` columns, so a substring test for "type" would fail on those.
    assert "u.type" not in body and "users.type" not in body, (
        "auth.resolve_claims reads auth.users.type — access must come only from "
        "auth.organisation_memberships"
    )


# ---------------------------------------------------------------------------
# WHAT IT IS FOR: the adviser gate `assign_adviser` never had.
# ---------------------------------------------------------------------------


async def test_only_a_login_marked_adviser_may_be_given_a_clients_book(
    service_session: AsyncSession,
) -> None:
    """The hole this closes, in both of its mouths.

    `assign_adviser` validated only `_user_in_tenant`, so ANY login in the firm could be put on a
    client's book — a client's own login included, since a client with portal access is a row in
    the same table. And 0123 then began landing a firm's whole custodian employee directory as
    placeholder logins, every one of which became assignable.

    Since 0245 a team also holds staff who are not the adviser — so the adviser role asks
    `is_adviser`, every other role asks `staff`, and a client's login or a placeholder is
    refused in any role.
    """
    tid = await _firm(service_session)
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, type, email)"
                    " VALUES (:t, 'individual', 'client@nature.test') RETURNING id"
                ),
                {"t": tid},
            )
        ).scalar_one()
    )
    adviser = await _login(service_session, tid, "adviser@nature.test", "{staff}", is_adviser=True)
    plain_staff = await _login(service_session, tid, "paraplanner@nature.test", "{staff}")
    a_client = await _login(service_session, tid, "portal@nature.test", "{client}")
    untagged = await _login(service_session, tid, "unknown@nature.test", "{}")

    assigned = await contacts_mod.assign_adviser(
        service_session, tenant_id=tid, contact_id=contact_id, adviser_id=adviser
    )
    assert assigned["role"] == "adviser"

    for who, what in (
        (a_client, "a client's own login"),
        (plain_staff, "a colleague nobody has said advises"),
        (untagged, "a login whose nature nobody has stated"),
    ):
        with pytest.raises(LookupError, match="not marked as an adviser"):
            await contacts_mod.assign_adviser(
                service_session, tenant_id=tid, contact_id=contact_id, adviser_id=who
            )
        assert what  # names the case in the failure output

    # The refusal NAMES THE FIX, because "not an adviser" is a real colleague and not a wrong id —
    # two states that need two different actions from whoever hit the message.
    with pytest.raises(LookupError, match="Turn on 'Adviser' on their team page"):
        await contacts_mod.assign_adviser(
            service_session, tenant_id=tid, contact_id=contact_id, adviser_id=plain_staff
        )

    # The same colleague joins the team in a staff role…
    joined = await contacts_mod.assign_adviser(
        service_session,
        tenant_id=tid,
        contact_id=contact_id,
        adviser_id=plain_staff,
        role="paraplanner",
    )
    assert joined["role"] == "paraplanner"
    # …but a client's login and a placeholder are not staff, in any role.
    for who in (a_client, untagged):
        with pytest.raises(LookupError, match="not a member of staff"):
            await contacts_mod.assign_adviser(
                service_session,
                tenant_id=tid,
                contact_id=contact_id,
                adviser_id=who,
                role="support",
            )


async def test_a_deleted_adviser_may_not_be_named_a_clients_adviser(
    service_session: AsyncSession,
) -> None:
    """After 0245 the adviser is the ONE `role = 'adviser'` row. A deleted login in it would be
    named by every listing, while the readers that filter `deleted_at` (Morgan Lloyd, the AML
    team list) showed no adviser at all — so the adviser gate asks for a live login, as the
    staff gate already did."""
    tid = await _firm(service_session)
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, type, email)"
                    " VALUES (:t, 'individual', 'client2@nature.test') RETURNING id"
                ),
                {"t": tid},
            )
        ).scalar_one()
    )
    gone = await _login(service_session, tid, "left@nature.test", "{staff}", is_adviser=True)
    await service_session.execute(
        text("UPDATE auth.users SET deleted_at = now() WHERE id = :u"), {"u": gone}
    )
    with pytest.raises(LookupError, match="not marked as an adviser"):
        await contacts_mod.assign_adviser(
            service_session, tenant_id=tid, contact_id=contact_id, adviser_id=gone
        )
    with pytest.raises(LookupError, match="not a member of staff"):
        await contacts_mod.assign_adviser(
            service_session,
            tenant_id=tid,
            contact_id=contact_id,
            adviser_id=gone,
            role="support",
        )


async def test_switching_off_adviser_moves_their_clients_to_support(
    service_session: AsyncSession,
) -> None:
    """Only an `is_adviser` login may be a client's adviser (0245). Switching it off keeps the
    person on every team they were on, as support; the client is left with no adviser rather than
    one who may not hold a book. Their other roles are left alone."""
    tid = await _firm(service_session)
    leaving = await _login(service_session, tid, "leaving@nature.test", "{staff}", is_adviser=True)
    other = await _login(service_session, tid, "other@nature.test", "{staff}", is_adviser=True)
    contacts = []
    for _ in range(2):
        contacts.append(
            int(
                (
                    await service_session.execute(
                        text(
                            "INSERT INTO crm.contacts (tenant_id, type) "
                            "VALUES (:t, 'individual') RETURNING id"
                        ),
                        {"t": tid},
                    )
                ).scalar_one()
            )
        )
    advised, planned = contacts
    await contacts_mod.assign_adviser(
        service_session, tenant_id=tid, contact_id=advised, adviser_id=leaving
    )
    await contacts_mod.assign_adviser(
        service_session, tenant_id=tid, contact_id=planned, adviser_id=other
    )
    await contacts_mod.assign_adviser(
        service_session,
        tenant_id=tid,
        contact_id=planned,
        adviser_id=leaving,
        role="paraplanner",
    )

    await provisioning.update_member_profile(
        service_session, tenant_id=tid, user_id=leaving, fields={"is_adviser": False}
    )

    rows = (
        await service_session.execute(
            text(
                "SELECT contact_id, adviser_id::text, role FROM crm.client_advisers "
                "WHERE tenant_id = :t ORDER BY contact_id, role"
            ),
            {"t": tid},
        )
    ).all()
    assert [tuple(r) for r in rows] == [
        (advised, leaving, "support"),
        (planned, other, "adviser"),
        (planned, leaving, "paraplanner"),
    ]


async def test_the_profile_save_writes_only_what_it_is_given(
    service_session: AsyncSession,
) -> None:
    """The rule the catalogue's model form learned the hard way.

    A form that posts its whole shape clears every control it does not render — that is how saving
    a model's NAME came to wipe the VAT flag the platform had set. So a key the caller did not
    send must be left alone, while one sent as null must CLEAR: an adviser who no longer holds a
    designation and a field nobody touched are different intentions.
    """
    tid = await _firm(service_session)
    user_id = await _login(service_session, tid, "person@nature.test", "{staff}")

    out = await provisioning.update_member_profile(
        service_session,
        tenant_id=tid,
        user_id=user_id,
        fields={
            "is_adviser": True,
            "job_title": "Chartered Financial Planner",
            "qualifications": ["DipPFS", "Chartered FCSI"],
        },
    )
    assert out["is_adviser"] is True
    assert out["job_title"] == "Chartered Financial Planner"
    assert out["qualifications"] == ["DipPFS", "Chartered FCSI"]
    assert out["type"] == ["staff"], "untouched by a save that did not name it"

    # A save of one unrelated field leaves the rest standing.
    out = await provisioning.update_member_profile(
        service_session, tenant_id=tid, user_id=user_id, fields={"bio": "Twenty years in advice."}
    )
    assert out["job_title"] == "Chartered Financial Planner"
    assert out["is_adviser"] is True

    # An empty value CLEARS rather than storing "": every reader treats a blank string as present,
    # so an adviser card would draw an empty row under the name.
    out = await provisioning.update_member_profile(
        service_session, tenant_id=tid, user_id=user_id, fields={"job_title": "  "}
    )
    assert out["job_title"] is None


async def test_an_adviser_must_be_staff_and_the_refusal_says_so(
    service_session: AsyncSession,
) -> None:
    """`{client, adviser}` is not a person. Refused in the service so the message names the rule,
    with `users_adviser_is_staff` as the floor under it."""
    tid = await _firm(service_session)
    a_client = await _login(service_session, tid, "client@nature.test", "{client}")

    with pytest.raises(ValueError, match="an adviser is a member of staff"):
        await provisioning.update_member_profile(
            service_session, tenant_id=tid, user_id=a_client, fields={"is_adviser": True}
        )
    # …and stating the new side at the same time is fine, which is how a firm promotes somebody
    # in one save. It REPLACES rather than adds: `auth.sync_user_type_contract` refuses to make an
    # identity dual ("a client/staff identity must be split, not made dual"), so the two sides are
    # two logins and this one is now staff.
    out = await provisioning.update_member_profile(
        service_session,
        tenant_id=tid,
        user_id=a_client,
        fields={"is_adviser": True, "type": ["staff"]},
    )
    assert out["is_adviser"] is True
    assert out["type"] == ["staff"]

    with pytest.raises(ValueError, match="unknown type"):
        await provisioning.update_member_profile(
            service_session, tenant_id=tid, user_id=a_client, fields={"type": ["adviser"]}
        )
    with pytest.raises(LookupError):
        await provisioning.update_member_profile(
            service_session,
            tenant_id=tid,
            user_id="00000000-0000-0000-0000-000000000000",
            fields={"job_title": "x"},
        )


async def test_the_database_refuses_a_type_outside_the_vocabulary(
    service_session: AsyncSession,
) -> None:
    """The CHECK is the floor under `update_member_profile`'s own validation — a direct write, a
    future endpoint or a migration backfill all have to satisfy it."""
    tid = await _firm(service_session)
    with pytest.raises(Exception, match="users_type_values_check"):
        await service_session.execute(
            text(
                "INSERT INTO auth.users (tenant_id, email, enabled, type)"
                " VALUES (:t, 'bad@nature.test', true, ARRAY['owner'])"
            ),
            {"t": tid},
        )
    await service_session.rollback()


async def test_adviser_is_no_longer_a_member_of_the_type_vocabulary(
    service_session: AsyncSession,
) -> None:
    """It was one until 0125. As a third member it made `{client, adviser}` expressible, and there
    is no such person — so it became `is_adviser`, and the nonsense became unrepresentable."""
    tid = await _firm(service_session)
    with pytest.raises(Exception, match="users_type_values_check"):
        await service_session.execute(
            text(
                "INSERT INTO auth.users (tenant_id, email, enabled, type)"
                " VALUES (:t, 'old@nature.test', true, ARRAY['staff','adviser'])"
            ),
            {"t": tid},
        )
    await service_session.rollback()


async def test_the_database_refuses_an_adviser_who_is_not_staff(
    service_session: AsyncSession,
) -> None:
    """`users_adviser_is_staff`. Advising is a capability WITHIN staff, and the dependency is
    stated in the schema rather than only in the service that writes it."""
    tid = await _firm(service_session)
    with pytest.raises(Exception, match="users_adviser_is_staff"):
        await service_session.execute(
            text(
                "INSERT INTO auth.users (tenant_id, email, enabled, type, is_adviser)"
                " VALUES (:t, 'nostaff@nature.test', true, ARRAY['client'], true)"
            ),
            {"t": tid},
        )
    await service_session.rollback()


# ---------------------------------------------------------------------------
# THE FOLDED EMAIL (0125). Stored lowercase, so the ORIGINAL unique key is the whole mechanism.
# ---------------------------------------------------------------------------


async def test_a_mixed_case_address_cannot_be_stored_at_all(
    service_session: AsyncSession,
) -> None:
    """`users_email_is_folded` is the ENFORCEMENT, not a comment.

    Five paths write this column and a sixth would forget, so the constraint is what makes "we
    store it folded" true rather than aspirational: a path that does not normalise fails loudly
    here instead of silently creating a second login for one person — which is exactly the defect
    that was live in the invite flow before this.
    """
    tid = await _firm(service_session)
    with pytest.raises(Exception, match="users_email_is_folded"):
        await service_session.execute(
            text(
                "INSERT INTO auth.users (tenant_id, email, enabled)"
                " VALUES (:t, 'Ann.Adviser@firm.test', true)"
            ),
            {"t": tid},
        )
    await service_session.rollback()


async def test_one_address_is_one_login_and_the_original_key_is_what_says_so(
    service_session: AsyncSession,
) -> None:
    """0124 added an expression index for this; 0125 deleted it by folding the VALUE instead, so
    `users_tenant_email_key` (0001) does the whole job and every `ON CONFLICT (tenant_id, email)`
    in the estate is correct again."""
    tid = await _firm(service_session)
    await _login(service_session, tid, provisioning.fold_email("Ann.Adviser@firm.test"))
    with pytest.raises(Exception, match="users_tenant_email_key"):
        await service_session.execute(
            text("INSERT INTO auth.users (tenant_id, email, enabled) VALUES (:t, :e, true)"),
            {"t": tid, "e": provisioning.fold_email("  ANN.ADVISER@FIRM.TEST ")},
        )
    await service_session.rollback()


async def test_the_expression_index_is_gone(service_session: AsyncSession) -> None:
    """Folded storage plus the 0001 key is the same guarantee, so the second index carried nothing
    but a maintenance cost and an expression conflict target at every upsert."""
    found = (
        await service_session.execute(text("SELECT to_regclass('auth.users_tenant_email_ci_key')"))
    ).scalar_one()
    assert found is None


def test_folding_strips_whitespace_as_well_as_case() -> None:
    """A pasted address routinely carries a trailing space, and `' a@b.test'` is not `'a@b.test'`
    to any unique key."""
    assert provisioning.fold_email("  Ann.Adviser@Firm.Test  ") == "ann.adviser@firm.test"
    assert provisioning.fold_email(None) == ""
    assert provisioning.fold_email("") == ""


def test_every_write_path_routes_through_the_one_normaliser() -> None:
    """The chokepoint claim, read off the SOURCE rather than trusted.

    `auth.users.email` is written by five statements across two modules. The CHECK catches a path
    that forgets — but it catches it at run time, in whichever flow happens to exercise it first,
    and an invite that 500s is a worse way to learn this than a failing test.
    """
    import ast
    import inspect

    from app import events

    offenders: list[str] = []
    for module in (provisioning, events):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            # A dict entry keyed "email" whose value is not a `fold_email(...)` call.
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if not (isinstance(key, ast.Constant) and key.value == "email"):
                    continue
                # A SUBSCRIPT is a read out of a result row (`r["email"]` in
                # `members_with_portals`), not a value being bound into a statement. Excluded by
                # shape rather than by line number so the scan survives the file moving.
                if isinstance(value, ast.Subscript):
                    continue
                call = value if isinstance(value, ast.Call) else None
                name = ""
                if call is not None:
                    func = call.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name != "fold_email":
                    offenders.append(f"{module.__name__}:{key.lineno}")

    # The READ projections legitimately pass an email straight through (`members_with_portals`
    # returns `r["email"]`), so this is scoped to what is BOUND INTO A WRITE — a dict whose value
    # is a bare subscript or attribute is a read, and a `fold_email` call is a write done right.
    # Anything else is a write that forgot.
    assert not offenders, f"email bound into a statement without fold_email: {offenders}"


# ---------------------------------------------------------------------------
# THE MANUAL MAPPING — `provider_refs`, for the people the email match declined to guess at.
# ---------------------------------------------------------------------------


async def test_a_custodian_id_can_be_set_and_cleared_by_the_firm(
    service_session: AsyncSession,
) -> None:
    """`provisioning.set_provider_ref` — ONE implementation behind two routes.

    The operations route (`/admin/tenants/…`) and the firm's own (`/users/…`) differ in who may
    call them and how the tenant is established; what a mapping IS must not differ by who set it.

    The clearing branch is the half worth executing: a blank id must REMOVE the key, not store
    `{"sei": ""}`. An empty string in a vendor payload is not the same as sending nothing, and a
    drawer's prefill would read it as configured.
    """
    tid = await _firm(service_session)
    user_id = await _login(service_session, tid, "adviser@nature.test", "{staff}", is_adviser=True)

    out = await provisioning.set_provider_ref(
        service_session,
        tenant_id=tid,
        user_id=user_id,
        provider="sei",
        external_id="57817496",
    )
    assert out == {"provider": "sei", "provider_refs": {"sei": "57817496"}}

    # A second provider ADDS rather than replaces — one person can be known to two custodians.
    out = await provisioning.set_provider_ref(
        service_session,
        tenant_id=tid,
        user_id=user_id,
        provider="morgan-lloyd",
        external_id="a-guid",
    )
    assert out["provider_refs"] == {"sei": "57817496", "morgan-lloyd": "a-guid"}

    # Blank CLEARS that one key and leaves the other standing.
    out = await provisioning.set_provider_ref(
        service_session, tenant_id=tid, user_id=user_id, provider="sei", external_id="  "
    )
    assert out["provider_refs"] == {"morgan-lloyd": "a-guid"}

    # Clearing the last one leaves NULL rather than `{}` — `from_provider` in the members
    # projection tests `provider_refs IS NOT NULL`, so an empty object would read as "this row
    # came from a custodian".
    out = await provisioning.set_provider_ref(
        service_session, tenant_id=tid, user_id=user_id, provider="morgan-lloyd", external_id=""
    )
    assert out["provider_refs"] == {}
    held = (
        await service_session.execute(
            text("SELECT provider_refs FROM auth.users WHERE id = :u"), {"u": user_id}
        )
    ).scalar_one()
    assert held is None


async def test_setting_a_mapping_is_scoped_to_the_firm_and_the_provider_is_closed(
    service_session: AsyncSession,
) -> None:
    """Two refusals with different meanings.

    The tenant conjunct is the only thing stopping an operations caller (whose session is
    `service_role`, so RLS is not in play) writing a mapping onto another firm's staff.
    """
    one, two = await _firm(service_session), await _firm(service_session)
    theirs = await _login(service_session, two, "someone@other.test", "{staff}")

    with pytest.raises(LookupError, match="not a member of this firm"):
        await provisioning.set_provider_ref(
            service_session, tenant_id=one, user_id=theirs, provider="sei", external_id="1"
        )
    with pytest.raises(ValueError, match="unknown provider"):
        await provisioning.set_provider_ref(
            service_session, tenant_id=two, user_id=theirs, provider="acme", external_id="1"
        )

    # A CLEAR against a member who holds nothing is a no-op, not a 404: the statement returns NULL
    # both for "no such member" and for "had no refs", and only the first is an error.
    out = await provisioning.set_provider_ref(
        service_session, tenant_id=two, user_id=theirs, provider="sei", external_id=""
    )
    assert out["provider_refs"] == {}


class TestRemovingStaffFromAnAdviser:
    """The adviser-implies-staff guard asked only whether the SAVE wanted to make somebody an
    adviser, so narrowing `type` on an existing one slipped past both branches.

    `PUT /firm/members/{id}` with `{"type": ["client"]}` sends no `is_adviser` key at all — the
    route passes `exclude_unset=True` and the field is optional — so the write set
    `type = '{client}'` while leaving `is_adviser = true` and hit `users_adviser_is_staff` as a
    500 naming a constraint. Moving somebody off the staff side is the ordinary way a firm changes
    a job.
    """

    async def test_narrowing_type_on_an_adviser_is_refused_by_name(
        self, service_session: AsyncSession
    ) -> None:
        tid = await _firm(service_session)
        person = await _login(service_session, tid, "adviser@nature.test", "{staff}")
        await provisioning.update_member_profile(
            service_session, tenant_id=tid, user_id=person, fields={"is_adviser": True}
        )

        with pytest.raises(ValueError, match="an adviser is a member of staff"):
            await provisioning.update_member_profile(
                service_session, tenant_id=tid, user_id=person, fields={"type": ["client"]}
            )

    async def test_clearing_both_in_one_save_is_allowed(
        self, service_session: AsyncSession
    ) -> None:
        """…and the refusal names the way through, so a firm can actually make the change."""
        tid = await _firm(service_session)
        person = await _login(service_session, tid, "moved@nature.test", "{staff}")
        await provisioning.update_member_profile(
            service_session, tenant_id=tid, user_id=person, fields={"is_adviser": True}
        )

        out = await provisioning.update_member_profile(
            service_session,
            tenant_id=tid,
            user_id=person,
            fields={"type": ["client"], "is_adviser": False},
        )
        assert out["is_adviser"] is False
        assert out["type"] == ["client"]
