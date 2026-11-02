"""A tenant's slice of a provider's conformed table, as a DuckDB WHERE clause.

Ported from app/fanout.py rather than reinvented: every branch here was learned from a slice that
came out silently wrong, and the comments say which. The port is faithful except that it no longer
takes the provider name for the conformed path -- a conformed provider is filtered by containment,
full stop, and that is the whole point of docs/platform-model.md §3.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from book_sink.config import sql_str


class PredicateUnavailable(RuntimeError):
    """No slice can be expressed for this provider/config, so the tenant must not be projected."""


# SEI publishes its own tenancy dialect and has not conformed yet, so its per-axis shape survives.
# Kept as explicit sets rather than inferred: getting it wrong is silent OVER-projection -- one
# firm seeing another's rows -- and the shape of each table is a fact about the models.
# `transfer_providers` is global for the same reason as instruments: the Reference
# codes have a null firm_id on every row. Narrowing on the firm axis matches nobody.
_SEI_GLOBAL_TABLES = frozenset({"instruments", "transfer_providers"})
#: `users` joins `firms` here for the FOURTH occurrence of this shape. A SEI employee user has a
#: `FIRM_ID` and no subfirm and no DSR team — `UserDetail` carries twelve columns and neither axis
#: is among them — so applying the entity triple raises `Binder Error: Referenced column
#: "subfirm_id" not found` and aborts the whole tenant's projection the moment an operator picks a
#: subfirm, which the admin pickers exist to do. Staff are narrowed on the firm alone.
#: `models` joins them for the FIFTH time. A model is discovered from the accounts that name it and
#: is published per (model, firm) — a model used across two subfirms has no single one, so it
#: carries `firm_id` alone and narrowing on the finer axes would raise a Binder Error. Narrowed on
#: the firm, which is right: a firm's model is the firm's whichever of its teams uses it.
#: `fee_packages` joins them too: a periodic fee package carries `firm_id` alone (it is priced by
#: the firm, not a subfirm or DSR team), so applying the finer axes would raise a Binder Error.
#: Narrowed on the firm, which is right — a firm's fee register is the firm's.
_SEI_NO_SUBFIRM_TABLES = frozenset({"firms", "dsr_teams", "users", "models", "fee_packages"})
_SEI_NO_DSR_TABLES = frozenset({"firms", "subfirms", "users", "models", "fee_packages"})
_SEI_SCALAR_DSR_TABLES = frozenset({"dsr_teams"})
#: The org DIMENSION, which carries no tenancy triple at all. `platform.nodes` supersedes SEI's
#: `firms`/`subfirms`/`dsr_teams` (docs/platform-model.md §3) and publishes the conformed
#: `key`/`lineage` instead of a `firm_id`, so the triple raises `Binder Error: Referenced
#: column "firm_id" not found` and aborts the whole tenant — the same failure the sets above were
#: written for, one model rename later.
#:
#: Narrowed on the FIRM axis only, by containment on `lineage`: SEI's tree puts the firm root at
#: depth 1 and its subfirms and teams below it, so `list_has_any(lineage, [firm_id])` is the firm
#: and everything under it. Subfirm/DSR narrowing deliberately does NOT apply here, exactly as
#: `_SEI_NO_SUBFIRM_TABLES` already said for `firms`: this is a NAME table, an account's `key`
#: can point at any node of its firm's tree, and an org that is not projected is an unresolvable
#: parent — a counted reject that reads as missing data. It is all one firm's own structure.
_SEI_NODE_TABLES = frozenset({"nodes"})


def _in_list(column: str, values: Iterable[Any]) -> str:
    quoted = ", ".join(sql_str(str(value)) for value in values)
    return f"CAST({column} AS VARCHAR) IN ({quoted})"


def conformed(keys: Iterable[Any]) -> str:
    """One containment test, every entity, every conformed provider.

    Every conformed row carries `lineage` -- the root->leaf ancestry of the org unit that owns it
    -- so a tenant's slice is "any of my org keys appears in this row's path".

    An EMPTY lineage means global reference data (an instrument is not owned by a firm, and
    neither is a firm-wide fee register), which projects whole. Scoping such a row to the firm ROOT
    instead looks tighter and is wrong: containment tests ANCESTRY, so a root-owned row matches
    only a tenant holding the root, and every tenant on a sub-node gets none of its own firm's
    pricing -- which is exactly how platform.third_party_providers came to project 0 of 7 rows.
    """
    keys = [sql_str(str(key)) for key in keys]
    if not keys:
        # No keys means no slice. Returning `true` would hand this tenant the provider's whole
        # book; returning `false` would look like "this firm has no data". Refuse instead.
        raise PredicateUnavailable("enablement names no org keys, so its slice is undefined")
    return f"(len(lineage) = 0 OR list_has_any(lineage, [{', '.join(keys)}]))"


def sei(config: Mapping[str, Any], table: str) -> str:
    """The tenant slice of one SEI table, narrowed on the axes that table actually has.

    The entity tables all carry the same tenancy triple from platform.tenancy (firm_id,
    subfirm_id, dsr_team_ids), so one shape filters all of them. The DIMENSION tables do not:
    platform.firms has only firm_id, platform.subfirms has no dsr column, and platform.dsr_teams
    names its team `dsr_team_id` (one per row) rather than carrying a list. Applying the entity
    shape to those three raised `Binder Error: Referenced column "subfirm_id" not found` and
    aborted the whole tenant's projection the moment an operator picked any subfirm or DSR team --
    which the admin pickers exist to do.
    """
    if table in _SEI_GLOBAL_TABLES:
        return "true"
    if not config.get("firm_id"):
        raise PredicateUnavailable("SEI enablement names no firm_id")
    if table in _SEI_NODE_TABLES:
        return conformed([config["firm_id"]])
    clauses = [_in_list("firm_id", [config["firm_id"]])]
    if config.get("subfirm_ids") and table not in _SEI_NO_SUBFIRM_TABLES:
        # `clients` is the one entity whose own subfirm column is empty. RESIDING_SUBFIRM_ID is
        # blank on every row of the Clients report, so `subfirm_id IN (...)` matches nobody and
        # the firm's people never reach the book — which reads as "this firm has no clients".
        # The subfirm is the ACCOUNT's. `platform.clients.subfirm_ids` is that list, taken from
        # `platform.tenancy`, and a client holding an account at any enabled subfirm is in the
        # slice. A scalar pick would drop the other subfirm of the one client who holds two.
        if table == "clients":
            wanted = ", ".join(sql_str(str(value)) for value in config["subfirm_ids"])
            clauses.append(f"list_has_any(subfirm_ids, [{wanted}])")
        else:
            clauses.append(_in_list("subfirm_id", config["subfirm_ids"]))
    if config.get("dsr_ids"):
        if table in _SEI_SCALAR_DSR_TABLES:
            clauses.append(_in_list("dsr_team_id", config["dsr_ids"]))
        elif table not in _SEI_NO_DSR_TABLES:
            wanted = ", ".join(sql_str(str(value)) for value in config["dsr_ids"])
            clauses.append(f"list_has_any(dsr_team_ids, [{wanted}])")
    return " AND ".join(clauses)


def for_provider(provider: str, config: Mapping[str, Any], table: str) -> str:
    """The WHERE clause for one of a provider's conformed tables.

    Values are quoted; the config shape was validated at enablement (app/providers.py).
    """
    if provider == "seccl":
        return conformed(config["node_ids"])
    if provider == "morgan-lloyd":
        # ML's org axis is the single Microsoft tenant, so its conformed rows carry a one-element
        # lineage and containment filters them like any other conformed provider.
        return conformed(config["tenant_ids"])
    if provider == "sei":
        return sei(config, table)
    raise PredicateUnavailable(f"no slice defined for provider '{provider}'")
