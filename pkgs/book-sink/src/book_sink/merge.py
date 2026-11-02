"""Land a tenant's staged slice in the book, one entity at a time, and say what happened.

The policy lives in Postgres (platform.merge_entity / platform.merge_linked_entity, migrations 0064
and 0065): insert a row the first time the provider shows it, then only refresh what the provider
owns. This module is the ORCHESTRATION around it -- the order, the foreign keys resolved against the
book, and the ledger -- and it is deliberately the only part written in Python:

* the ORDER is a fact about the entities (a valuation needs its account), declared in entities.py;
* the PARENTS are resolved in the book, not in the lake, because an id is Graphene's and a lake
  model must never see one;
* the REJECTS are the whole point. app/materialise.py's ledger existed because a projection with no
  merge leaves a firm looking empty, which is indistinguishable from "this firm has no data with
  this provider" -- the reasons are the only thing that tells the two apart. They now persist
  (platform.merge_rejects) instead of living in one HTTP response.

ONE TRANSACTION PER ENTITY, not per tenant. A nightly batch that discards a firm's accounts because
one transaction carried an unmapped type is a worse outcome than a partial landing the ledger names:
the entities are ordered, so what committed is a prefix, and the next run resumes it.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from book_sink.banks import UnreadableBankAccounts, merged_bank_accounts
from book_sink.entities import Entity, Parent

logger = logging.getLogger("book_sink.merge")


class MergeUnavailable(RuntimeError):
    """The staged slice cannot be merged (no landing table, no such target)."""


@dataclass
class EntityLedger:
    """What one entity did. Mirrors app/materialise.py's EntityResult so the admin surfaces that
    read a materialise ledger read this one unchanged."""

    entity: str
    created: int = 0
    updated: int = 0
    rejected: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "entity": self.entity,
            "created": self.created,
            "updated": self.updated,
            "rejected": self.rejected,
            "reasons": self.reasons,
        }
        if self.note:
            record["note"] = self.note
        return record


#: Postgres truncates an identifier longer than this to exactly this, SILENTLY. A real tenant uid is
#: `ten_` + 32 hex = 36 characters, so `{uid}__third_party_providers` lands at 59 — four bytes of
#: headroom. At 40 characters two entities would truncate to the same name and one firm's staging
#: table would quietly become another entity's, which is the failure this whole pipeline keeps
#: finding: not an error, a wrong answer that looks like data.
_MAX_IDENTIFIER = 63


def _table_name(uid: str, entity: str) -> str:
    name = f"{uid}__{entity}"
    if len(name.encode()) > _MAX_IDENTIFIER:
        raise MergeUnavailable(
            f"landing table name '{name}' is {len(name.encode())} bytes; Postgres truncates at "
            f"{_MAX_IDENTIFIER} and two entities would then share one table. Shorten the uid "
            f"or hash it into the name."
        )
    return name


# A provider slug after `-` → `_`. Anything else is not a schema we will create, and interpolating
# it would be a statement, not a name.
_SCHEMA_SLUG = re.compile(r"[a-z][a-z0-9_]*")


def landing_schema(provider: str) -> str:
    """The schema this provider lands in: `inbound_seccl`, `inbound_sei`, `inbound_morgan_lloyd`.

    Not a column on a shared table, and not a token in the table name. The table name is already
    `{uid}__third_party_providers` at 60 of Postgres's 63 bytes, so a provider token cannot fit
    there — two entities would truncate to one table and the failure would look like data. The
    schema is a separate identifier. One shared `inbound` is how SEI's merge, three seconds after
    Seccl's, inserted Seccl's clients under `provider = 'sei'` (27 Sep 2026): ReplaceInPlace had
    overwritten the only table, and the merge stamps whatever it finds with the provider it was
    called as.
    """
    slug = provider.replace("-", "_")
    if _SCHEMA_SLUG.fullmatch(slug) is None:
        raise MergeUnavailable(f"provider {provider!r} is not a landing-schema name")
    return f"inbound_{slug}"


def landing_relation(uid: str, entity: str, provider: str) -> str:
    """Where the staging model delivers, unquoted -- interlace's `target_ref` splits a target on
    `.` and quotes the parts itself, so a quoted name here would become part of the identifier.

    One schema per provider, the uid in the NAME, rather than a schema per tenant: `authenticated`
    is granted nothing on these schemas, and three unreachable schemas are easier to keep
    unreachable than fifty.
    """
    return f"{landing_schema(provider)}.{_table_name(uid, entity)}"


def landing_table(uid: str, entity: str, provider: str) -> str:
    """The same relation, quoted, for SQL we build ourselves.

    The quoting is not decoration: a tenant uid is `encode(gen_random_bytes(8), 'hex')`, so it
    begins with a digit about six times in ten, and `inbound_seccl.401d0a...__orgs` is not an
    identifier at all -- Postgres reads it as "trailing junk after numeric literal". Unquoted,
    this pipeline would have worked for some firms and refused to exist for the rest.
    """
    return f'{landing_schema(provider)}."{_table_name(uid, entity)}"'


def _alias(parent: Parent) -> str:
    return f"p_{parent.column}"


def _literal(value: int | str) -> str:
    """A value inlined into SQL, quoted.

    Needed because the resolved source is a VIEW, and a view definition cannot carry parameters --
    `CREATE TEMP VIEW … WHERE l.tenant_id = $1` fails with "could not determine data type of
    parameter". Both values are ours (an integer tenant id and a provider name from our own
    registry), and both are quoted anyway rather than trusted.
    """
    if isinstance(value, int):
        return str(value)
    return "'" + value.replace("'", "''") + "'"


def _resolution(parent: Parent, alias: str, tenant_id: int, provider: str) -> tuple[str, str]:
    """(join clause, selected expression) for one parent, resolved in the book."""
    scope = f"{alias}.tenant_id = {_literal(tenant_id)} AND {alias}.provider = {_literal(provider)}"
    if parent.via.startswith("link:"):
        entity_type = parent.via.split(":", 1)[1]
        join = (
            f"LEFT JOIN platform.links {alias}"
            f" ON {scope}"
            f" AND {alias}.entity_type = {_literal(entity_type)} AND {alias}.link_type = 'primary'"
            f" AND {alias}.external_id = s.{parent.source}"
        )
        return join, f"{alias}.entity_id AS {parent.column}"
    if parent.via == "node":
        join = f"LEFT JOIN platform.nodes {alias} ON {scope} AND {alias}.key = s.{parent.source}"
        return join, f"{alias}.id AS {parent.column}"
    if parent.via.startswith("unique:"):
        # A tenant-scoped row resolved by its own unique column: the firm's shelf by product_type,
        # a fee structure by the provider's fee id. The TARGET side is cast to text so an enum
        # column compares against staged text -- an unmodelled wrapper is then a NULL join rather
        # than a failed cast, which is the difference between a reject and a dead entity.
        _, table, column = parent.via.split(":", 2)
        join = (
            f"LEFT JOIN {table} {alias}"
            f" ON {alias}.tenant_id = {_literal(tenant_id)}"
            f" AND CAST({alias}.{column} AS text) = s.{parent.source}"
        )
        return join, f"{alias}.id AS {parent.column}"
    if parent.via == "platform":
        # NO JOIN. The provider IS the value now (0131): `platform.platforms` was a surrogate id
        # for a member of a closed vocabulary, and every column that pointed at it holds
        # `platform.provider_code` instead. This used to resolve `provider -> id` against a table
        # the API seeded on startup, and before it seeded it the join found nothing — so EVERY
        # product was rejected for EVERY provider, so no account carried a `product_id`, so
        # drawdown answered "pension accounts only" for demonstrable SIPPs. A value that cannot
        # fail to resolve cannot fail that way again.
        return "", f"CAST({_literal(provider)} AS platform.provider_code) AS {parent.column}"
    raise MergeUnavailable(f"unknown parent resolution '{parent.via}'")


# Two firms can merge at once. An adoptable catalogue is one row shared by every firm, so the
# later merge must see the row the earlier one has just written. The lock is held to the end of
# the merge transaction: the second firm waits, then adopts, instead of inserting a duplicate
# that the unique key refuses — which fails that firm's whole entity.
_ADOPT_LOCK = "book_sink.adopt"


def _hold_shared_catalogue(cursor: Any, target: str) -> None:
    cursor.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
        (_ADOPT_LOCK, target),
    )


def _adoption_checks(
    entity: Entity, source: str, tenant_id: int, provider: str
) -> list[tuple[str, str]]:
    """What an ADOPTABLE row must be true of, given that the shared entity it names is one row.

    platform.instruments is deduped by ISIN, so a provider publishing two of its own ids for one
    ISIN cannot have both mapped -- and inserting the second is not an option: instruments_isin_key
    refuses it and takes the whole entity down with it. Two ways that arises, one reason:

    * the ISIN is already in the book, mapped to a DIFFERENT id of this provider's (the adoption
      pass ran first, so what is left here is the genuine collision);
    * the same drop stages it twice, and neither is in the book yet -- the lowest id wins, which is
      arbitrary but stable, and the other is named rather than silently dropped.
    """
    if not entity.adopt_on or not entity.link_type:
        return []
    reason = f"'{', '.join(entity.adopt_on)} already mapped to another {provider} id'"
    present = " AND ".join(f"s.{column} IS NOT NULL" for column in entity.adopt_on)
    in_book = " AND ".join(f"t.{column} = s.{column}" for column in entity.adopt_on)
    in_drop = " AND ".join(f"s2.{column} = s.{column}" for column in entity.adopt_on)
    # Both are SQL fragments over table names from this package's own declarations and values
    # quoted by _literal, which is what makes the S608 suppression accurate rather than a habit.
    return [
        (
            f"NOT EXISTS (SELECT 1 FROM {entity.target} t"  # noqa: S608
            f"  JOIN platform.links l ON l.entity_id = t.id"
            f"   AND l.tenant_id = {_literal(tenant_id)} AND l.provider = {_literal(provider)}"
            f"   AND l.entity_type = {_literal(entity.link_type)} AND l.link_type = 'primary'"
            f" WHERE {in_book} AND {present} AND l.external_id <> s.external_id::text)",
            reason,
        ),
        (
            f"NOT EXISTS (SELECT 1 FROM {source} s2"  # noqa: S608
            f" WHERE {in_drop} AND {present} AND s2.external_id < s.external_id)",
            reason,
        ),
    ]


def _requirements(
    entity: Entity, source: str, tenant_id: int, provider: str
) -> list[tuple[str, str]]:
    """(check, reason) for every condition a staged row must meet, parents included."""
    checks = [(requirement.check, requirement.reason) for requirement in entity.requires]
    checks.extend(_adoption_checks(entity, source, tenant_id, provider))
    for parent in entity.parents:
        if not parent.required:
            continue
        if parent.via == "platform":
            # NOTHING TO REQUIRE. 0131 made this parent a VALUE rather than a row, so it cannot
            # be unresolved — and asserting `p_provider.id IS NOT NULL` against an alias that no
            # longer joins anything is a "missing FROM-clause entry", which the ledger records as
            # a failed ENTITY rather than a rejected row: every product for every provider, read
            # as "this firm has no data with this custodian".
            #
            # That is the same reading the requirement was written for. The failure it guarded
            # against was `platform.platforms` having never been written to, and a value cannot
            # fail to be seeded, which is why the table went.
            continue
        alias = _alias(parent)
        column = "entity_id" if parent.via.startswith("link:") else "id"
        # The reason names the provider's own key, which is what makes it chaseable: the ledger
        # records the KEY, never the row -- a rejected transaction is still client data and this
        # table is not a quarantine.
        reason = (
            f"'unresolved {parent.column}: ' || coalesce(s.{parent.source}::text, '(null)')"
            if parent.source
            # A parent with no key from the provider can only fail one way, and it is worth saying
            # plainly: platform.platforms had never been written to at all, which rejected every
            # product for every provider and left the whole book with no product_id.
            else f"'unresolved {parent.column}: nothing registered for {provider}'"
        )
        checks.append((f"{alias}.{column} IS NOT NULL", reason))
    return checks


def _source_sql(entity: Entity, source: str, tenant_id: int, provider: str) -> tuple[str, str]:
    """(select list, from+joins) for the resolved view over the landing table."""
    selected = ["s.*"]
    joins: list[str] = []
    for parent in entity.parents:
        alias = _alias(parent)
        join, expression = _resolution(parent, alias, tenant_id, provider)
        # A parent resolved from a VALUE contributes no join (0131's `platform`), and an empty
        # string joined into the FROM clause would leave a blank line mid-statement rather than
        # break it — but filtering keeps the generated SQL readable in a reject's context.
        if join:
            joins.append(join)
        selected.append(expression)
    return ", ".join(selected), f"FROM {source} s\n" + "\n".join(joins)


def merge_entity(
    cursor: Any, entity: Entity, *, uid: str, tenant_id: int, provider: str
) -> EntityLedger:
    """Merge one staged entity. The caller owns the transaction."""
    ledger = EntityLedger(entity=entity.slug)
    source = landing_table(uid, entity.slug, provider)
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (source,))
    row = cursor.fetchone()
    if not row or not row[0]:
        # Not an error: a provider that publishes no such entity, or a staging model that has not
        # run yet. Said out loud, because "no rows" and "no table" are different answers.
        ledger.note = f"not staged: {source}"
        return ledger

    params = {"tenant_id": tenant_id, "provider": provider, "entity": entity.slug}

    # 1. Adoption, FIRST -- an entity with an identity of its own outside this provider is adopted,
    #    never duplicated. platform.instruments is global and deduped by ISIN, so two firms holding
    #    the same fund must reach the same row: the link is minted against what already exists and
    #    the merge then declines to insert a second one. It runs before the rejects because whether
    #    a row is admissible DEPENDS on it -- what is left unadopted is a genuine collision.
    #    The lock is what makes that true when both firms merge the fund in the same sync: without
    #    it both adoption passes see an empty book and the second insert is refused.
    if entity.adopt_on and entity.link_type:
        _hold_shared_catalogue(cursor, entity.target)
        match = " AND ".join(
            f"t.{column} = s.{column} AND s.{column} IS NOT NULL" for column in entity.adopt_on
        )
        cursor.execute(
            f"INSERT INTO platform.links"  # noqa: S608 - identifiers are this module's own
            f"   (tenant_id, entity_type, entity_id, provider, link_type, external_id, data)"
            f" SELECT DISTINCT ON (s.external_id)"
            f"   %(tenant_id)s, %(entity_type)s, t.id, %(provider)s, 'primary',"
            f"   s.external_id::text, cast(s.data AS jsonb)"
            f" FROM {source} s JOIN {entity.target} t ON {match}"
            f" WHERE NOT EXISTS ("
            f"   SELECT 1 FROM platform.links l"
            f"    WHERE l.tenant_id = %(tenant_id)s AND l.provider = %(provider)s"
            f"      AND l.entity_type = %(entity_type)s AND l.link_type = 'primary'"
            f"      AND l.external_id = s.external_id::text)"
            f" ORDER BY s.external_id"
            # Two staged rows can name the same ISIN. DO NOTHING makes the second a skipped
            # duplicate rather than "cannot affect row a second time", and _adoption_check then
            # records it as a reject instead of letting it insert a duplicate instrument.
            f" ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type) DO NOTHING",
            {**params, "entity_type": entity.link_type},
        )

    select_list, from_sql = _source_sql(entity, source, tenant_id, provider)
    checks = _requirements(entity, source, tenant_id, provider)

    # 2. The rejects, before anything else is written: one row per refusal, naming the source key.
    if checks:
        failing = " OR ".join(f"NOT ({check})" for check, _ in checks)
        reason = (
            "CASE "
            + " ".join(f"WHEN NOT ({check}) THEN {reason}" for check, reason in checks)
            + " END"
        )
        cursor.execute(
            # Identifiers are this module's own; every value is a bound parameter. RETURNING is what
            # makes the count exact -- reading the reasons back by timestamp would either miss a
            # slow run's rows or pick up the previous one's.
            f"INSERT INTO platform.merge_rejects"  # noqa: S608
            f" (tenant_id, provider, entity, reason, row_key)"
            f" SELECT %(tenant_id)s, %(provider)s, %(entity)s, {reason}, s.{entity.row_key}::text"
            f" {from_sql} WHERE {failing}"
            f" RETURNING reason",
            params,
        )
        for (recorded,) in cursor.fetchall():
            ledger.rejected += 1
            # Pooled, not per row: "117 unmapped transactions" is one line an operator can act on.
            ledger.reasons[str(recorded)] = ledger.reasons.get(str(recorded), 0) + 1

    # 3. The resolved, admissible rows, as a view the merge functions can take as a regclass.
    #    A VIEW cannot carry parameters, which is why the scoping values are inlined by _literal:
    #    `CREATE TEMP VIEW … WHERE l.tenant_id = $1` fails with "could not determine data type of
    #    parameter", and only for the entities that HAVE a parent -- so it would have looked like a
    #    contacts bug rather than a view rule.
    view = f"src_{entity.slug}"
    keep = " AND ".join(f"({check})" for check, _ in checks) if checks else "true"
    cursor.execute(f"DROP VIEW IF EXISTS pg_temp.{view}")
    cursor.execute(
        f"CREATE TEMP VIEW {view} AS SELECT {select_list} {from_sql} WHERE {keep}"  # noqa: S608
    )
    # READABLE BY `service_role`, because a SECURITY DEFINER merge function runs as its OWNER and
    # a temp view belongs to the pipeline's own login — so `auth.merge_provider_user` failed with
    # "permission denied for view src_users" while every SECURITY INVOKER merge worked.
    #
    # Granting the VIEW is the narrow half of that trade. The alternative was making the function
    # SECURITY INVOKER and giving `book_writer` DML on `auth.users`, which would let the pipeline
    # write ANY column of a real login — `enabled`, an email, a WorkOS id — where the definer
    # arrangement gives it exactly the three statements the function performs. `service_role`
    # already holds ALL on every relation this view is derived from, so this grants it nothing it
    # could not already read.
    cursor.execute(f"GRANT SELECT ON pg_temp.{view} TO service_role")

    # 4. The observations: counted, but the rows still go. `rejected` deliberately does not move,
    #    because these rows ARE written — a count in `reasons` with no reject is how the ledger says
    #    "we used this row and here is what we could not use of it".
    for observation in entity.observes:
        cursor.execute(
            f"SELECT count(*), {observation.reason} FROM {view} s"  # noqa: S608
            f" WHERE NOT ({observation.check}) GROUP BY 2",
        )
        for count, noted in cursor.fetchall():
            ledger.reasons[str(noted)] = ledger.reasons.get(str(noted), 0) + int(count)

    # 5. The merge itself -- the policy, in the database.
    if entity.procedure:
        # An entity whose write the generic policy cannot express: a model version is a decision
        # about a SET of rows, keyed by max(version) + 1. Same signature, so the ledger is uniform.
        cursor.execute(
            f"SELECT inserted, updated FROM {entity.procedure}("  # noqa: S608 - own declarations
            f"  %(view)s::regclass, %(tenant_id)s, %(provider)s)",
            {**params, "view": view},
        )
    elif entity.link_type:
        cursor.execute(
            "SELECT inserted, updated FROM platform.merge_linked_entity("
            "  %(view)s::regclass, %(target)s::regclass, %(entity_type)s,"
            "  %(tenant_id)s, %(provider)s, %(updatable)s, %(exclude)s)",
            {
                **params,
                "view": view,
                "target": entity.target,
                "entity_type": entity.link_type,
                "updatable": list(entity.updatable),
                # Kept out of the entity, still read for the link (0070).
                "exclude": list(entity.exclude),
            },
        )
    else:
        cursor.execute(
            "SELECT inserted, updated FROM platform.merge_entity("
            "  %(view)s::regclass, %(target)s::regclass, %(key)s, %(updatable)s,"
            "  %(conflict_where)s)",
            {
                **params,
                "view": view,
                "target": entity.target,
                "conflict_where": entity.conflict_where,
                "key": list(entity.key),
                # Passed through exactly as declared: an EMPTY tuple is the insert-only policy
                # (0067), not an omission to be defaulted back to {data}.
                "updatable": list(entity.updatable),
            },
        )
    inserted, updated = cursor.fetchone()
    ledger.created = int(inserted)
    ledger.updated = int(updated)
    # After the insert-only write. A parent that was NULL because it did not exist yet is not a
    # value the firm chose, so the run that can finally resolve it fills the column. A column
    # that already holds an id is left alone.
    ledger.updated += _fill_unresolved_parents(
        cursor, entity, view, tenant_id=tenant_id, provider=provider
    )
    if entity.target == "platform.accounts":
        _record_client_products(cursor, view, tenant_id=tenant_id, provider=provider)
        _merge_sei_bank_accounts(cursor, view, tenant_id=tenant_id, provider=provider)
    cursor.execute(f"DROP VIEW IF EXISTS pg_temp.{view}")
    return ledger


# Landing columns arrive as text. COALESCE will not match text to a typed book
# column, so these fills name the book's type. Every other fill column is text.
_FILL_CASTS: dict[str, str] = {
    "ocf": "numeric(18, 10)",
    "is_vulnerable": "boolean",
    "us_person": "boolean",
}


def _filled(column: str) -> str:
    cast = _FILL_CASTS.get(column)
    if cast is None:
        return f"s.{column}"
    return f"cast(s.{column} AS {cast})"


def _fill_unresolved_parents(
    cursor: Any, entity: Entity, view: str, *, tenant_id: int, provider: str
) -> int:
    """Fill parent columns that are still NULL, and return how many rows that touched.

    An account can land before its client: the parent is optional, so the row is written with a
    NULL `contact_id`, and the insert-only policy then never reads that column again. The same
    is true of `product_id` and `model_id` — the account comment already says the next run
    resolves them. A NULL is the unresolved case. An id already stored is the firm's, and a
    later drop naming a different parent does not move it.

    `replace_placeholder` is the same idea for a column whose first sighting stored the
    provider's id because the provider had not named it. The book's value still equalling
    that id is the unresolved case. Any other value is the firm's.

    `replace_numeric` is the same idea for a column whose first sighting stored a code.
    A stored value that is only digits is that code. A word is the firm's, and is left
    alone. The staged value may itself be NULL (an unset code), which clears the number.
    """
    owned = set(entity.updatable)
    # `*` means the generic merge already writes every column, including clearing one it
    # cannot resolve. Filling nulls on top of that would put back what that policy removed.
    if "*" in owned:
        return 0
    columns = [
        parent.column
        for parent in entity.parents
        if parent.column not in owned and parent.via != "platform"
    ]
    columns.extend(column for column in entity.fill_if_null if column not in owned)
    # The comparison is against the link's external id, so a natural-key entity has nothing
    # to compare. Models are linked; this is the case it exists for.
    placeholders = [
        column for column in entity.replace_placeholder if column not in owned and entity.link_type
    ]
    numerics = [column for column in entity.replace_numeric if column not in owned]
    if not columns and not placeholders and not numerics:
        return 0
    assignments = ", ".join(
        [f"{column} = COALESCE(t.{column}, {_filled(column)})" for column in columns]
        + [f"{column} = s.{column}" for column in placeholders]
        + [f"{column} = s.{column}" for column in numerics]
    )
    pending = " OR ".join(
        [f"(t.{column} IS NULL AND s.{column} IS NOT NULL)" for column in columns]
        + [
            f"(t.{column}::text = l.external_id"
            f" AND s.{column} IS NOT NULL"
            f" AND s.{column}::text IS DISTINCT FROM t.{column}::text)"
            for column in placeholders
        ]
        + [
            f"(t.{column}::text ~ '^[0-9]+$'"
            f" AND t.{column}::text IS DISTINCT FROM coalesce(s.{column}::text, ''))"
            for column in numerics
        ]
    )
    if entity.link_type:
        frm = (
            f"FROM {view} s JOIN platform.links l"
            f" ON l.tenant_id = %(tenant_id)s AND l.provider = %(provider)s"
            f" AND l.entity_type = %(entity_type)s AND l.link_type = 'primary'"
            f" AND l.external_id = s.external_id::text"
            f" WHERE t.id = l.entity_id AND ({pending})"
        )
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "provider": provider,
            "entity_type": entity.link_type,
        }
    elif entity.key:
        # Both sides as text. The view's key columns are the landing table's text; the book's
        # are a date, an enum, an int. Comparing them uncast is a failed entity, not a skipped row.
        # tenant_id is not part of every natural key (transactions are keyed by the provider's
        # own id), and book_writer bypasses RLS, so the fill has to say which firm itself.
        parts = [f"t.{column}::text = s.{column}::text" for column in entity.key]
        if "tenant_id" not in entity.key:
            parts.insert(0, "t.tenant_id::text = s.tenant_id::text")
        where = " AND ".join(parts)
        frm = f"FROM {view} s WHERE {where} AND ({pending})"
        params = {}
    else:
        return 0
    cursor.execute(
        f"UPDATE {entity.target} t SET {assignments} {frm}",  # noqa: S608 - own declarations
        params,
    )
    return int(cursor.rowcount)


def _record_client_products(cursor: Any, view: str, *, tenant_id: int, provider: str) -> None:
    """Store a Seccl client product on the contact, one link per wrapper.

    ``product_external_id`` is ``wrapperDetail.clientProductId``. It is not a column of
    ``platform.accounts``. Every account of one wrapper shares it, so the link lives on
    the contact as ``client_product:{wrapper}``. A later drop that names a different id
    updates the link. A provider that publishes the column as NULL writes nothing, and a
    landing table from before the column existed is left alone.
    """
    cursor.execute(
        "SELECT 1 FROM pg_attribute"
        " WHERE attrelid = %s::regclass AND attname = 'product_external_id'"
        "   AND attnum > 0 AND NOT attisdropped",
        (view,),
    )
    if cursor.fetchone() is None:
        return
    cursor.execute(
        # The view name is this module's. Every value is bound.
        f"INSERT INTO platform.links"  # noqa: S608
        f" (tenant_id, entity_type, entity_id, provider, link_type, external_id)"
        f" SELECT DISTINCT ON (s.contact_id, s.product_key)"
        f"   %(tenant_id)s, 'contact', s.contact_id,"
        f"   %(provider)s::platform.provider_code,"
        f"   'client_product:' || s.product_key, s.product_external_id"
        f" FROM {view} s"
        f" WHERE s.contact_id IS NOT NULL"
        f"   AND nullif(btrim(s.product_external_id), '') IS NOT NULL"
        f"   AND nullif(btrim(s.product_key), '') IS NOT NULL"
        f" ORDER BY s.contact_id, s.product_key, s.product_external_id"
        f" ON CONFLICT (tenant_id, provider, entity_type, entity_id, link_type)"
        f" DO UPDATE SET external_id = EXCLUDED.external_id"
        f" WHERE platform.links.external_id IS DISTINCT FROM EXCLUDED.external_id",
        {"tenant_id": tenant_id, "provider": provider},
    )


def _merge_sei_bank_accounts(cursor: Any, view: str, *, tenant_id: int, provider: str) -> None:
    """Refresh SEI pay-to profiles on the linked accounts. Other providers publish NULL
    and must not be read as "this account has no profiles"."""
    if provider != "sei":
        return
    cursor.execute(
        "SELECT 1 FROM pg_attribute"
        " WHERE attrelid = %s::regclass AND attname = 'bank_accounts'"
        "   AND attnum > 0 AND NOT attisdropped",
        (view,),
    )
    if cursor.fetchone() is None:
        return
    cursor.execute(
        f"SELECT l.entity_id, t.bank_accounts, s.bank_accounts"  # noqa: S608
        f" FROM {view} s"
        f" JOIN platform.links l"
        f"   ON l.tenant_id = %(tenant_id)s AND l.provider = %(provider)s"
        f"  AND l.entity_type = 'account' AND l.link_type = 'primary'"
        f"  AND l.external_id = s.external_id::text"
        f" JOIN platform.accounts t ON t.id = l.entity_id",
        {"tenant_id": tenant_id, "provider": provider},
    )
    for entity_id, existing, incoming in cursor.fetchall():
        try:
            merged = merged_bank_accounts(existing, incoming)
        except UnreadableBankAccounts:
            logger.warning(
                "skipped bank merge for account %s: staged value was not a list", entity_id
            )
            continue
        payload = None if merged is None else json.dumps(merged)
        cursor.execute(
            "UPDATE platform.accounts"
            " SET bank_accounts = %s::jsonb"
            " WHERE id = %s"
            "   AND bank_accounts IS DISTINCT FROM %s::jsonb",
            (payload, entity_id, payload),
        )


def merge_tenant(
    connection: Any,
    entities: tuple[Entity, ...],
    *,
    uid: str,
    tenant_id: int,
    provider: str,
    enablement_id: int | None = None,
) -> list[dict[str, Any]]:
    """Merge every staged entity for one (tenant, provider), in order, and return the ledger.

    A failed entity is recorded and the walk CONTINUES: the entities after it may not depend on it
    (transactions do not need instruments to have landed), and stopping the tenant dead would hide
    every later entity behind one provider's bad column.
    """
    ledger: list[dict[str, Any]] = []
    for entity in entities:
        try:
            with connection.transaction(), connection.cursor() as cursor:
                ledger.append(
                    merge_entity(
                        cursor, entity, uid=uid, tenant_id=tenant_id, provider=provider
                    ).as_dict()
                )
        except Exception as error:  # noqa: BLE001 - recorded per entity, see docstring
            logger.warning(
                "book_sink: %s failed for tenant %s / %s: %s",
                entity.slug,
                uid,
                provider,
                error,
            )
            ledger.append(
                {
                    "entity": entity.slug,
                    "created": 0,
                    "updated": 0,
                    "rejected": 0,
                    "reasons": {},
                    "error": str(error),
                }
            )
    _record_run(connection, ledger, tenant_id=tenant_id, provider=provider)
    if enablement_id is not None:
        with connection.transaction(), connection.cursor() as cursor:
            # The admin tenant page reads this as the last merge; a pipeline that wrote the book
            # and left the timestamp alone would read as one that never ran.
            # BOTH columns, for one release. `last_merged_at` (0079) is what the value has
            # always meant — this is the merge stamping it — and `last_fanout_at` is the name it
            # carried from the deleted three-hop path. Expand phase: the contract phase drops the
            # old one once nothing reads it.
            cursor.execute(
                "UPDATE platform.tenant_platforms "
                "   SET last_fanout_at = now(), last_merged_at = now() "
                " WHERE id = %s",
                (enablement_id,),
            )
    return ledger


_RUN_INSERT = """
INSERT INTO platform.merge_runs
       (run_id, tenant_id, provider, entity, created, updated, rejected, reasons, note)
VALUES (%(run_id)s, %(tenant_id)s, %(provider)s, %(entity)s,
        %(created)s, %(updated)s, %(rejected)s, %(reasons)s, %(note)s)
"""


def _record_run(
    connection: Any, ledger: list[dict[str, Any]], *, tenant_id: int, provider: str
) -> None:
    """Persist the ledger to `platform.merge_runs` (0076).

    ITS OWN TRANSACTION, and failure is swallowed. The ledger describes work that is already
    committed -- each entity landed in its own transaction above -- so a book that merged
    correctly must not be reported as failed because the record of it could not be written. The
    reverse costs an operator one run's visibility and is logged.

    One `run_id` for the whole walk, minted here rather than defaulted in the column: every entity
    of one merge shares it, and a default would give each row its own and turn a run into N
    unrelated rows.
    """
    if not ledger:
        return
    run_id = str(uuid.uuid4())
    try:
        with connection.transaction(), connection.cursor() as cursor:
            for row in ledger:
                cursor.execute(
                    _RUN_INSERT,
                    {
                        "run_id": run_id,
                        "tenant_id": tenant_id,
                        "provider": provider,
                        "entity": row.get("entity"),
                        "created": int(row.get("created", 0)),
                        "updated": int(row.get("updated", 0)),
                        "rejected": int(row.get("rejected", 0)),
                        "reasons": json.dumps(row.get("reasons") or {}),
                        # `error` on a failed entity, `note` where an entity sets one.
                        "note": row.get("note") or row.get("error"),
                    },
                )
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning(
            "book_sink: could not record the merge run for tenant %s / %s", tenant_id, provider
        )
