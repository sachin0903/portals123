"""The migration chain's own shape — the invariants that actually matter.

WHY THIS EXISTS. The four-digit prefix on a migration looks like a position in the chain and is
NOT one: 26 numbers are used by more than one migration (`0042` by three). That is not damage, it
is the signature of parallel branches later merged -- six `*_merge_*` revisions in this directory
record it -- and the numbers were assigned per branch before those merges existed. Renumbering
them was considered and declined: a revision id is quoted ~1,300 times across the migrations,
the docs and code comments, is what `alembic_version` stores in every deployed database, and is
cited in tickets and PRs that live outside this repo. So the number is a creation-order HINT and
`alembic history` is the authority.

That leaves the invariants a duplicate number cannot tell you about, and none of them was checked:

  * ONE HEAD. Two heads means `alembic upgrade head` refuses, and the failure arrives at deploy
    time rather than at review time.
  * UNIQUE revision ids. Duplicate NUMBERS are survivable; duplicate IDS are not -- alembic
    resolves a revision by id, so two files claiming one id makes the chain ambiguous.
  * THE FILENAME IS THE ID, so a reference in a doc or a comment finds the file. Drift is listed
    below rather than allowed by a rule, so a second one has to be a decision.
  * `version_num` IS VARCHAR(32). An id longer than that inserts fine locally and fails on the
    first deployment that stamps it -- which is why some ids are abbreviations rather than
    mistakes, and why this is asserted rather than left to be rediscovered.
  * THE DOCSTRING AGREES WITH THE CODE. Every file states `Revises:` in prose beside its real
    `down_revision`, and `0097_portal_applications` named a revision two steps back -- a reader
    reconstructing the order from the docstrings would have placed it wrong.
  * EVERY FILE IS REACHABLE. A migration nothing points at never runs, and nothing else notices:
    the chain is valid, the head is right, and the table it was written to create is simply absent.

These are read off the files rather than from a live database, so they cost nothing and run
everywhere. `tests/test_rls.py` and friends are what prove the chain actually APPLIES.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"

#: `alembic_version.version_num` is `VARCHAR(32)`. Alembic creates the table itself, so this is
#: not ours to widen without a migration on the migration table -- an id over the limit is a
#: deploy-time failure, not a local one.
MAX_ID_LENGTH = 32

#: Filename stem longer than 32 characters, so the ID is abbreviated to fit `version_num`. The
#: FILE keeps the descriptive name (it is what the docs cite and what a sibling `.sql` is
#: called). Listed rather than allowed by a rule, so a second one has to be a decision.
ABBREVIATED = {
    "0119_access_profiles_and_multi_roles": "0119_access_profiles_roles",
    "0151_product_platform_role_cardinality": "0151_product_role_cardinality",
    "0155_account_role_registered_contact": "0155_registered_contact",
    "0156_jisa_registered_contact_scope": "0156_jisa_contact_scope",
    "0168_registered_contact_relationship_type": "0168_registered_contact_rel",
    "0169_registered_contact_relationships": "0169_registered_contact_links",
    "0173_sipp_onboarding_illustration": "0173_sipp_illustration",
    "0183_transaction_reference_search": "0183_txn_reference_search",
    "0185_transaction_effective_date_indexes": "0185_txn_effective_date_idx",
    "0242_transaction_statement_columns": "0242_stmt_columns",
}

#: Filename still disagrees with the id even though the stem fits. Historical; do not add to
#: this list -- rename the file to match the id, or abbreviate only because of `version_num`.
ALIASES = {
    "0092_notification_rule_versions": "0092_rule_versions",
    "0094_documents_status_cleanup": "0094_docs_status_cleanup",
    "0095_audit_log_aml_carveout": "0095_audit_aml_carveout",
    "0187_compliance_provider_columns": "0187_provider_columns",
    "0189_gia_isa_illustrations": "0189_gia_isa_illustration",
    "0240_contact_mobile_and_phone": "0240_contact_mobile_phone",
}

#: `Revises:` in the module docstring names a different parent than `down_revision`. The code
#: is the chain; the prose was never kept in lockstep. New migrations must not join this list.
DOCSTRING_DRIFT = {
    "0097_portal_applications",
    "0098_portal_from_application",
    "0099_portal_host_unique",
    "0100_firm_org",
    "0101_resolve_claims_multi_env",
    "0103_app_secrets",
    "0107_app_only_portal",
    "0108_drop_org_portal_columns",
    "0110_nanoid",
    "0221_complycube_core",
}

_DOCSTRING_REVISES = re.compile(r"^Revises: (.+)$", re.M)


def _assigned(tree: ast.Module, name: str) -> object:
    """The literal assigned to a module-level name, or None.

    Read with `ast` rather than a regex because the annotation is not uniform: a merge revision
    declares `down_revision: tuple[str, ...] | None = (...)` and one spells it
    `str | Sequence[str] | None = (` across two lines. A regex that matched only the common
    `str | None` shape silently read those as "no parent" — which is a FALSE BASE, and the head
    and reachability checks below would then pass while describing a chain that does not exist.
    """
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            target = first.id if isinstance(first, ast.Name) else None
        if target != name or node.value is None:
            continue
        return ast.literal_eval(node.value)
    return None


class Migration:
    __slots__ = ("stem", "revision", "down", "declared_revises")

    def __init__(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        self.stem = path.stem
        revision = _assigned(tree, "revision")
        assert isinstance(revision, str), f"{path.name} declares no string `revision`"
        self.revision = revision
        down = _assigned(tree, "down_revision")
        if down is None:
            self.down: tuple[str, ...] = ()
        elif isinstance(down, str):
            self.down = (down,)
        else:
            self.down = tuple(down)  # type: ignore[arg-type]
        said = _DOCSTRING_REVISES.search(ast.get_docstring(tree) or "")
        self.declared_revises = said.group(1).strip() if said else None


@pytest.fixture(scope="module")
def migrations() -> list[Migration]:
    found = [Migration(p) for p in sorted(VERSIONS.glob("*.py")) if p.name != "__init__.py"]
    assert len(found) > 100, "found almost no migrations -- this test has gone stale, not passed"
    return found


def test_revision_ids_are_unique(migrations: list[Migration]) -> None:
    """A duplicate NUMBER is history; a duplicate ID is an ambiguous chain."""
    seen: dict[str, list[str]] = {}
    for m in migrations:
        seen.setdefault(m.revision, []).append(m.stem)
    clashes = {rev: files for rev, files in seen.items() if len(files) > 1}
    assert not clashes, f"two migrations claim one revision id: {clashes}"


def test_a_revision_id_fits_the_version_column(migrations: list[Migration]) -> None:
    """`VARCHAR(32)`, and the longest ids in the tree are exactly 32 -- so this is not slack."""
    too_long = {m.revision: len(m.revision) for m in migrations if len(m.revision) > MAX_ID_LENGTH}
    assert not too_long, (
        f"revision ids longer than alembic's version_num column ({MAX_ID_LENGTH}): {too_long}. "
        "Abbreviate the ID and keep the descriptive FILENAME (see ABBREVIATED)."
    )


def test_the_filename_is_the_revision_id(migrations: list[Migration]) -> None:
    """So a citation finds the file. The exceptions are named, with the reason."""
    drifted = {
        m.stem: m.revision
        for m in migrations
        if m.stem != m.revision
        and ABBREVIATED.get(m.stem) != m.revision
        and ALIASES.get(m.stem) != m.revision
    }
    assert not drifted, (
        f"filename and revision id disagree: {drifted}. Rename the file to match the id, or -- if "
        f"the name is over {MAX_ID_LENGTH} characters -- add it to ABBREVIATED with the reason."
    )


def test_the_abbreviation_list_is_not_stale(migrations: list[Migration]) -> None:
    """An entry for a file that no longer needs one stops meaning anything."""
    by_stem = {m.stem: m for m in migrations}
    for stem, abbreviated in ABBREVIATED.items():
        m = by_stem.get(stem)
        assert m is not None, f"ABBREVIATED names {stem}, which is not a migration"
        assert m.revision == abbreviated, f"{stem} no longer uses the abbreviated id {abbreviated}"
        assert len(stem) > MAX_ID_LENGTH, (
            f"{stem} is {len(stem)} characters and fits; give it its own name as the id and drop "
            "the exception."
        )


def test_the_alias_list_is_not_stale(migrations: list[Migration]) -> None:
    """A historical alias that now matches, or a stem that grew past 32, is a listed lie."""
    by_stem = {m.stem: m for m in migrations}
    for stem, aliased in ALIASES.items():
        m = by_stem.get(stem)
        assert m is not None, f"ALIASES names {stem}, which is not a migration"
        assert m.revision == aliased, f"{stem} no longer uses the aliased id {aliased}"
        assert stem != aliased, f"{stem} now matches its id; drop it from ALIASES"
        assert len(stem) <= MAX_ID_LENGTH, (
            f"{stem} is over {MAX_ID_LENGTH} characters; move it to ABBREVIATED"
        )


def test_there_is_exactly_one_head(migrations: list[Migration]) -> None:
    """Two heads and `alembic upgrade head` refuses -- at deploy time, not at review time."""
    parents = {p for m in migrations for p in m.down}
    heads = sorted(m.revision for m in migrations if m.revision not in parents)
    assert len(heads) == 1, f"expected one head, found {len(heads)}: {heads}"


def test_every_migration_is_reachable_from_base(migrations: list[Migration]) -> None:
    """A migration nothing points at never runs, and the chain still looks perfectly healthy."""
    children: dict[str, list[str]] = {}
    for m in migrations:
        for parent in m.down or (None,):  # type: ignore[arg-type]
            children.setdefault(parent, []).append(m.revision)
    seen: set[str] = set()
    queue = list(children.get(None, []))
    while queue:
        rev = queue.pop()
        if rev in seen:
            continue
        seen.add(rev)
        queue.extend(children.get(rev, []))
    orphans = sorted(m.stem for m in migrations if m.revision not in seen)
    assert not orphans, f"unreachable from base, so they never run: {orphans}"


def test_every_down_revision_resolves(migrations: list[Migration]) -> None:
    known = {m.revision for m in migrations}
    dangling = {m.stem: [p for p in m.down if p not in known] for m in migrations}
    dangling = {k: v for k, v in dangling.items() if v}
    assert not dangling, f"down_revision naming a revision that does not exist: {dangling}"


def test_the_docstring_agrees_with_the_code(migrations: list[Migration]) -> None:
    """`Revises:` in the prose header is how a reader reconstructs the order without alembic.

    `0097_portal_applications` said `0092_…` while actually following `0096_…` — five steps of
    chain, invisible to every other check here because the code was right. Those files are
    named in `DOCSTRING_DRIFT`; a new disagreement is a fail.
    """
    wrong = {}
    for m in migrations:
        if m.stem in DOCSTRING_DRIFT or m.declared_revises is None:
            continue
        said = [s.strip() for s in re.split(r"[,+]| and ", m.declared_revises) if s.strip()]
        if said == ["None"] and not m.down:
            continue
        if sorted(said) != sorted(m.down):
            wrong[m.stem] = {"docstring": said, "down_revision": list(m.down)}
    assert not wrong, f"the prose header disagrees with down_revision: {wrong}"


def test_the_docstring_drift_list_is_not_stale(migrations: list[Migration]) -> None:
    by_stem = {m.stem: m for m in migrations}
    for stem in DOCSTRING_DRIFT:
        m = by_stem.get(stem)
        assert m is not None, f"DOCSTRING_DRIFT names {stem}, which is not a migration"
        said = [s.strip() for s in re.split(r"[,+]| and ", m.declared_revises or "") if s.strip()]
        assert sorted(said) != sorted(m.down), (
            f"{stem} docstring now agrees with down_revision; drop it from DOCSTRING_DRIFT"
        )
