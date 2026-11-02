"""Every table the book's pipeline owns is still written by it.

This gated Phase 7's deletion by reading `app/materialise.py`'s own source. That module is gone, so
the comparison is gone with it — but the failure it existed to catch is not: one table stops
being written, the pipeline runs green, and every surface reading it shows an empty state that is
indistinguishable from a firm with nothing there — the confusion this whole re-architecture was
undertaken to end.

So `OWNED` below is the set materialise.py wrote, FROZEN at the commit that deleted it (17 tables,
read from its `INSERT INTO`s), and every one must still be reachable from `book_sink` — as an entity
target, a procedure's table, or one the merge policy owns. A hand-maintained list is weaker than
reading a second implementation, and it is what remains once there is only one; the compensation is
that adding to it is deliberate, and the tests below still derive the NEW side from the code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

book_sink = pytest.importorskip("book_sink")

#: What `app/materialise.py` wrote, read from its source on the commit that deleted it. Every one of
#: these is a table some surface reads, so a pipeline that stops filling one shows an empty state
#: rather than an error.
OWNED: frozenset[str] = frozenset(
    {
        "crm.contacts",
        "platform.accounts",
        "platform.fee_package_platforms",
        "platform.fee_packages",
        "platform.instruments",
        "platform.links",
        "platform.model_allocations",
        "platform.model_commentary",
        "platform.model_versions",
        "platform.models",
        "platform.nodes",
        "platform.product_platforms",
        "platform.products",
        "platform.third_party_providers",
        "platform.transactions",
        "platform.transfer_providers",
        "platform.valuations",
    }
)

#: Tables a book_sink PROCEDURE writes rather than an entity target (0068). Named here because the
#: procedures are SQL and their INSERTs cannot be read off the entity declarations.
PROCEDURE_TARGETS = {
    "platform.model_versions",
    "platform.model_allocations",
    "platform.model_commentary",
}

#: Written by the merge functions themselves, for every linked entity.
MERGE_TARGETS = {"platform.links"}


def _pipeline_writes() -> set[str]:
    targets = {entity.target for entity in book_sink.CONFORMED}
    return targets | PROCEDURE_TARGETS | MERGE_TARGETS


def test_the_old_path_is_gone() -> None:
    """The deletion this file used to gate. Asserted rather than assumed, so a revert that restored
    the module without restoring the comparison does not leave a false green."""
    assert not (Path(__file__).resolve().parent.parent / "app" / "materialise.py").exists()


def test_every_table_the_pipeline_owns_is_still_written() -> None:
    missing = OWNED - _pipeline_writes()
    assert not missing, (
        f"book_sink writes no {sorted(missing)} — some surface reads "
        f"{'it' if len(missing) == 1 else 'them'} and would show an empty state, not an error"
    )


#: Written by the new path and never by the old one, each with a reason. Not a general licence: a
#: target with no old-path counterpart is one nothing has ever compared, so it is listed here
#: deliberately rather than allowed by omission.
NEW_TARGETS = {
    # 0063 dropped `platform.models.platform_identifier` as "superseded by
    # platform.model_platforms" — and then nothing ever wrote the successor, so a model's
    # per-platform reference had no home at all. Writing it completes that migration. Nothing READS
    # it yet, which is a known state and not a reason to leave the replacement empty.
    "platform.model_platforms",
    # 0119. The container BELOW an account, which the old path had no concept of at all: SEI's
    # `portfolioId` is required by four of its write paths and the only home it ever had was a
    # `platform.links` row, which permits exactly one — while 59 of 1,104 accounts in the drop carry
    # a Capital AND an Income portfolio. There is nothing to compare against because the old path
    # never wrote it; what makes it safe is that the account parent is REQUIRED, so a portfolio
    # whose account did not materialise is a counted reject rather than an unaddressable row.
    "platform.portfolios",
    # 0123, and the only target here that is not a `platform.*` table. A custodian's own employee
    # users are merged into `auth.users`, ATTACHING the provider's id to the login that already
    # holds the same email and otherwise inserting a disabled placeholder. Nothing compared it
    # because nothing ever wrote it: 0118 argued a custodian's people should NOT be rows in this
    # table, and this reverses that with the collision rule made explicit.
    #
    # What makes writing IDENTITY from the pipeline safe is that `book_writer` still holds no
    # privilege on the table — `auth.merge_provider_user` is SECURITY DEFINER, so the pipeline can
    # execute exactly those three statements and nothing else — and that every ambiguous case
    # (two logins on one email, an id already mapped elsewhere, no email at all) goes to
    # `platform.merge_rejects` rather than being resolved by guessing.
    "auth.users",
}


def test_the_pipeline_writes_nothing_unaccounted_for() -> None:
    """A target beyond the frozen set is one nothing ever compared against a second implementation,
    so each is named with its reason rather than allowed by omission. This is the half that still
    derives from the code, and it is what keeps the frozen list above from going quietly stale."""
    extra = _pipeline_writes() - OWNED - NEW_TARGETS
    assert not extra, f"targets nothing has ever compared: {sorted(extra)}"


def test_no_declared_new_target_is_also_an_owned_one() -> None:
    """A stale allowlist entry would hide a real divergence behind a comment."""
    stale = NEW_TARGETS & OWNED
    assert not stale, f"{sorted(stale)} is in OWNED now — drop it from NEW_TARGETS"


def test_every_entity_declares_how_it_is_merged() -> None:
    """Three ways, and exactly one per entity: a natural key, a link, or a named procedure. With
    none an entity is silently never written; with two, the ledger cannot say which ran."""
    for entity in book_sink.CONFORMED:
        ways = [bool(entity.key), bool(entity.link_type), bool(entity.procedure)]
        assert sum(ways) == 1, f"{entity.slug} declares {sum(ways)} merge strategies, not 1"


def test_a_linked_entity_is_one_the_book_has_no_key_for() -> None:
    """The four entities that carry an identity of their own, and the reason they are linked rather
    than keyed: one contact legitimately exists at both SEI and Morgan Lloyd, so a per-provider
    unique key on the row would force two rows for one person (0065)."""
    linked = {entity.target for entity in book_sink.CONFORMED if entity.link_type}
    assert linked == {
        "crm.contacts",
        "platform.accounts",
        "platform.models",
        "platform.instruments",
    }
