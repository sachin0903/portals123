"""The public-id contract, and the tripwires that keep it.

Two things are pinned here. First, the shape: a public ``uid`` is 12 characters of
``app/ids.py``'s alphabet with no prefix. Second — and this is the half that rots without a test —
the ONE kind deliberately held back, the tenant uid, which is still prefixed hex because it becomes
an S3 bucket name and a SQL schema identifier.

A previous attempt at this conversion was reverted because it moved a SUBSET of the mint sites for
four uid kinds, so a row's id shape came to depend on which endpoint created it. The scans below
exist to make that state fail a gate rather than ship.

There are two ways to mint a uid and both are scanned. Python-side is an f-string interpolating a
randomness call. **SQL-side is a string literal containing `gen_random_uuid()`** — invisible to any
AST scan of Python expressions, which is how three of them (`auth.users`, `notifications.rules`,
`notifications.rule_versions`) survived the first sweep of this branch and left two kinds with two
shapes each. A reseed found them; this test is what would have.

**What the scans deliberately do NOT cover:** randomness that is not interpolated into an f-string
(a bare ``uuid.uuid4().hex`` assigned to a variable, as in ``app/logging.py``'s correlation id).
Those are not the prefixed-uid shape and reading them would mean guessing at intent. The
``_uid``-by-name ban below is the second net under that gap.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app.ids import ALPHABET, ID_LENGTH, new_id, tenant_model_key, tenant_uid

API_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Call names that produce randomness. A uid built by interpolating one of these into an f-string
#: is a hand-rolled minter, whatever its prefix is spelled from -- which is the point: the first
#: version of this scan looked for a `doc_`-shaped string CONSTANT and so missed all fourteen
#: helpers whose prefix was a variable (`f"{prefix}_{uuid4().hex}"`, whose only constant is "_").
RANDOMNESS = {"uuid4", "uuid1", "token_hex", "token_urlsafe", "token_bytes", "choice", "choices"}

#: Modules allowed to interpolate randomness into an f-string, each with the reason it is not a
#: platform public id. Anything else is a new hand-rolled minter and should call `new_id()`.
MINT_ALLOWLIST = {
    # The minter itself, including the held-back tenant kind.
    "app/ids.py",
    # An object-store service user's ACCESS KEY, not a row's public id -- the store's own
    # identifier for a credential, and the tenant it belongs to is deliberately readable in it.
    "app/storage_credentials.py",
    # A demo fixture, not a client-facing record: `seed_demo`'s own re-run deletes its rows with
    # `uid LIKE 'demo\_%'`, so the prefix is a working delete-selector. Converting these means
    # giving cleanup another way to recognise its own rows first.
    "scripts/seed_demo.py",
}

#: Modules allowed to name the held-back tenant prefix. `ids.py` mints it; `tenant_storage` strips
#: it to build the bucket name. A third one appearing means the kind has grown a second shape.
TENANT_PREFIX_ALLOWLIST = {"app/ids.py", "app/tenant_storage.py"}


def _sources() -> list[tuple[str, str, ast.Module]]:
    out = []
    for root in ("app", "scripts"):
        for path in sorted((API_ROOT / root).rglob("*.py")):
            rel = path.relative_to(API_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            out.append((rel, text, ast.parse(text)))
    return out


def _call_names(node: ast.AST) -> set[str]:
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def test_new_id_shape() -> None:
    """A public id is ID_LENGTH characters drawn from ALPHABET, and nothing else."""
    ids = [new_id() for _ in range(500)]
    assert all(len(value) == ID_LENGTH for value in ids)
    assert set("".join(ids)) <= set(ALPHABET)
    # Not a proof of entropy, just that it is not returning a constant.
    assert len(set(ids)) == len(ids)


def test_alphabet_is_alphanumeric_only() -> None:
    """62 distinct symbols -- nanoid's default set MINUS `_` and `-`.

    The two are URL-safe but not safe everywhere a uid ends up: `app/lake/holdings.py` and
    `app/platform_entities.py` interpolate one into a SQL identifier behind an `^[A-Za-z0-9_]+$`
    guard that refuses `-`. Asserting the absence explicitly, because "URL-safe" is the reason
    someone would put them back.
    """
    assert len(ALPHABET) == 62
    assert len(set(ALPHABET)) == 62
    assert re.fullmatch(r"[A-Za-z0-9]+", ALPHABET)
    assert "_" not in ALPHABET and "-" not in ALPHABET


def test_the_two_minters_agree_on_the_alphabet() -> None:
    """`public.nanoid()`'s DEFAULT alphabet, read out of the migration, against `app/ids.py`'s.

    There are two minters -- the application and a column DEFAULT -- and every SQL call site passes
    `size` alone, so that default IS what the database mints. Nothing else ties them together, and
    a uid minted by the application and one minted by a column default becoming different kinds of
    string is the exact failure this whole module exists to prevent.
    """
    sql = (SQL_DIR / "0110_nanoid.sql").read_text(encoding="utf-8")
    match = re.search(r"alphabet text DEFAULT '([^']*)'", sql)
    assert match, "0110_nanoid.sql: no `alphabet text DEFAULT` found -- has the signature moved?"
    assert sorted(match.group(1)) == sorted(ALPHABET), (
        f"public.nanoid() defaults to {len(match.group(1))} symbols and app/ids.py mints "
        f"{len(ALPHABET)} -- change them together"
    )


def test_tenant_uid_stays_bucket_and_identifier_legal() -> None:
    """The held-back kind. If this fails, a firm's bucket cannot be created.

    `tenant_storage._bucket_name` strips `ten_` and puts the rest in an S3 bucket name, where `_`,
    `-` and uppercase are all illegal; `app/lake/holdings.py` and `app/platform_entities.py` put
    the whole uid in a SQL identifier behind an `^[A-Za-z0-9_]+$` guard that refuses `-`.
    """
    for _ in range(50):
        uid = tenant_uid()
        assert uid.startswith("ten_"), "the tenant kind keeps its prefix; tenant_storage strips it"
        assert re.fullmatch(r"^[A-Za-z0-9_]+$", uid), "the lake's identifier guard would refuse it"
        assert re.fullmatch(r"^[a-z0-9]+$", uid.removeprefix("ten_")), "illegal in a bucket name"


def test_tenant_model_key_strips_the_held_back_prefix() -> None:
    """DAG / lake identifiers are `tenant_<hex>`, not `tenant_ten_<hex>`."""
    assert tenant_model_key("ten_3b54affd38444414") == "3b54affd38444414"
    assert tenant_model_key("aaa1") == "aaa1", "a uid without the prefix is unchanged"


def test_no_hand_rolled_minters() -> None:
    """No module interpolates randomness into an f-string outside the allowlist."""
    offenders = []
    for rel, _text, tree in _sources():
        if rel in MINT_ALLOWLIST:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            for part in node.values:
                if isinstance(part, ast.FormattedValue) and _call_names(part) & RANDOMNESS:
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "these build an id by hand instead of calling app.ids.new_id(): " + ", ".join(offenders)
    )


def test_no_sql_side_minters() -> None:
    """No SQL string mints an id with `gen_random_uuid()` / `uuid_generate_v4()`.

    A set-based `INSERT ... SELECT` is the honest reason to reach for one -- it needs a fresh id per
    ROW. The answer is to pin the source rows and pair each with a Python-minted uid through
    `unnest(...)`, as `notifications/rules_service.py::clone_rules` now does, so `app/ids.py` stays
    the only minter and there is no second alphabet to drift.
    """
    offenders = []
    for rel, _text, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                low = node.value.lower()
                if "gen_random_uuid" in low or "uuid_generate_v4" in low:
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "these mint an id in SQL; pair rows with app.ids.new_id() via unnest instead: "
        + ", ".join(sorted(set(offenders)))
    )


SQL_DIR = API_ROOT / "migrations" / "sql"

#: Migrations that mint a uid in SQL by hand, and may keep doing so: they are HISTORY. Each
#: appends `encode(gen_random_bytes(8), 'hex')` inside the merge procedure's dynamic SQL, which is
#: what `0113_uid_defaults` stopped doing -- but an applied migration is a record of what ran, not
#: a place to correct. A FOURTH file here means the shape has been reintroduced.
SQL_MINT_ALLOWLIST = frozenset(
    {
        "0065_merge_linked_entity.sql",
        "0067_merge_entity_options.sql",
        "0070_merge_linked_exclude.sql",
    }
)


def test_a_uid_default_is_always_nanoid() -> None:
    """A migration may not default a `uid` column to anything but `nanoid(...)`.

    This is the guard the divergence got past. `app/ids.py` has always been the application's one
    minter, and both tripwires above scan Python -- so nothing was watching SQL, and three shapes
    grew there: 16 hex characters in the merge procedures, prefixed hex (`cnv_`/`grp_`/`att_`) as
    the messaging columns' DEFAULT, and nanoid(12) in Python. A prefix is its own problem: nothing
    resolves a uid without already knowing its table, so it labels a distinction no reader needs --
    and `con_` once meant both `crm.contacts` and `platform.contributions`, failing at exactly the
    distinction it existed to make.
    """
    scanned = sorted(SQL_DIR.glob("*.sql"))
    # A scan that reaches no files is not a guard (the lesson route-addressing.test.ts records).
    assert len(scanned) > 100, f"only {len(scanned)} migrations scanned — is SQL_DIR right?"
    offenders = []
    for path in scanned:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("--"):
                continue
            # `\buid\b` on BOTH sides: "uuid" ends in "uid", so a `uuid PRIMARY KEY DEFAULT
            # uuid_generate_v4()` column matched a one-sided boundary — five of them do.
            match = re.search(r"\buid\b[^;]*?\bDEFAULT\s+(?P<expr>[^;,)]+)", line, re.I)
            if match and "nanoid" not in match.group("expr").lower():
                offenders.append(f"{path.name}:{lineno} -> {match.group('expr').strip()}")
    assert not offenders, (
        "a uid column may only default to nanoid(12) -- see 0110_nanoid and app/ids.py: "
        + ", ".join(offenders)
    )


def test_no_sql_side_hex_uid_minters() -> None:
    """Outside the historical merge migrations, no SQL mints a uid from raw bytes.

    `encode(gen_random_bytes(8), 'hex')` is 16 characters of a 16-symbol alphabet where a uid is 12
    of 64 -- not merely a different spelling but a different identifier, and the one the provider
    pipeline was writing into `platform.accounts.uid` while every portal wrote the other.
    """
    offenders = []
    for path in sorted(SQL_DIR.glob("*.sql")):
        if path.name in SQL_MINT_ALLOWLIST:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("--"):
                continue
            if "uid" in line and "gen_random_bytes" in line:
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, (
        "these mint a uid in SQL from raw bytes; a uid is nanoid(12): " + ", ".join(offenders)
    )


def test_the_sql_minter_allowlist_still_matches_something() -> None:
    """An allowlist entry that no longer matches is a licence nobody is using -- and the next
    migration to mint hex would be waved through if a filename drifted onto it."""
    for name in SQL_MINT_ALLOWLIST:
        path = SQL_DIR / name
        assert path.exists(), name
        assert "gen_random_bytes" in path.read_text(encoding="utf-8"), name


def test_only_ids_module_defines_a_uid_minter() -> None:
    """The per-module `_uid(prefix)` helper is gone and does not come back.

    Fourteen copies of it existed, which is how the shape drifted in the first place -- and why a
    scan that only counts helper CALLS undercounts: half the mint sites were inline f-strings.
    """
    offenders = []
    for rel, _text, tree in _sources():
        if rel == "app/ids.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in {"_uid", "new_uid", "_new_uid"}:
                offenders.append(f"{rel}:{node.lineno} ({node.name})")
    assert not offenders, "public ids are minted in app/ids.py alone: " + ", ".join(offenders)


def test_tenant_prefix_named_in_one_place() -> None:
    """`ten_` appears only where it is minted and where it is stripped.

    A third site means the held-back kind has grown a second shape -- the exact failure that got
    the first attempt at this reverted.
    """
    offenders = [
        rel
        for rel, text, _tree in _sources()
        if rel not in TENANT_PREFIX_ALLOWLIST and '"ten_' in text
    ]
    assert not offenders, "mint a tenant uid with app.ids.tenant_uid(): " + ", ".join(offenders)


@pytest.mark.parametrize("module", sorted(MINT_ALLOWLIST | TENANT_PREFIX_ALLOWLIST))
def test_allowlisted_modules_exist(module: str) -> None:
    """An allowlist entry for a deleted file silently stops protecting anything."""
    assert (API_ROOT / module).is_file(), f"stale allowlist entry: {module}"


def _api_routes() -> list[object]:
    from fastapi.routing import APIRoute

    from app.main import app

    def walk(routes: object) -> object:
        for r in routes:  # type: ignore[attr-defined]
            if isinstance(r, APIRoute):
                yield r
            inner = getattr(r, "original_router", None)
            if inner is not None:
                yield from walk(inner.routes)

    return list(walk(app.routes))


def _declared(dependant: object) -> dict[str, object]:
    """Every path param the route binds, including through a dependency.

    The addressing dependencies (`app/addressing.py`) consume `{tenant_uid}`/`{contact_uid}` and
    friends, so they sit one level down in FastAPI's dependant tree rather than on the endpoint --
    reading only `dependant.path_params` sees an empty list for most of the API.
    """
    names = {p.name: p.field_info.annotation for p in dependant.path_params}  # type: ignore[attr-defined]
    for sub in dependant.dependencies:  # type: ignore[attr-defined]
        names |= _declared(sub)
    return names


def test_no_api_path_names_a_serial() -> None:
    """No route takes an `int` in its PATH.

    The rule `route-addressing.test.ts` enforces in the portals, enforced on the API's own side.
    A serial in a path publishes the row count and lets a caller walk the table by arithmetic, and
    it forced every form action and standalone `+server.ts` to resolve a uid before it could call
    anything -- a round trip per mutation, and a whole record fetched to keep one integer.
    """
    offenders = []
    for route in _api_routes():
        types = _declared(route.dependant)  # type: ignore[attr-defined]
        for name in re.findall(r"\{([a-z_]+)\}", route.path):  # type: ignore[attr-defined]
            if getattr(types.get(name), "__name__", "") == "int":
                offenders.append(f"{sorted(route.methods)[0]} {route.path} -> {{{name}}}")  # type: ignore[attr-defined]
    assert not offenders, (
        "these take a database serial in their path; resolve the uid with a dependency from "
        "app/addressing.py instead: " + ", ".join(sorted(offenders))
    )


def test_every_path_param_is_actually_read() -> None:
    """A `{param}` in a route's path must be bound by the handler or one of its dependencies.

    FastAPI does not mind an unread path param, which is how `GET /admin/tenants/{tenant_uid}/
    catalogue/terms` shipped declaring `tenant_id: int` -- a required QUERY parameter -- while
    ignoring the uid in its own path. Every caller using the router's addressing got a 422, and an
    integer passed by hand read ANY firm's terminology with no existence check. The route looked
    right in the diff and typechecked; nothing was watching the binding.
    """
    offenders = []
    for route in _api_routes():
        types = _declared(route.dependant)  # type: ignore[attr-defined]
        for name in re.findall(r"\{([a-z_]+)\}", route.path):  # type: ignore[attr-defined]
            if name not in types:
                offenders.append(f"{sorted(route.methods)[0]} {route.path} -> {{{name}}}")  # type: ignore[attr-defined]
    assert not offenders, (
        "these declare a path parameter nothing binds -- the value is unreachable and the route "
        "answers 422 or reads the wrong record: " + ", ".join(sorted(offenders))
    )
