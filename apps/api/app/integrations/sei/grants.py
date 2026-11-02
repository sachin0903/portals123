"""SEI servicing-team grants for a new client and a new account.

The operator saves team ids (`dsr_ids`, `custody_dsrs`). The catalogue says which
relationship id each scope already uses for that team. Those resolved pairs are
what enrolment sends. They are not written back onto the tenant config: a later
fill would be unable to tell a derived empty map from a key the operator never
set, and would fall through to the most-used grant on the book.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Grant:
    """One team grant the book already holds, on one scope.

    ``type_name`` is empty when the row came from a caller that only has the
    ids. A relationship is never invented from a missing name.
    """

    scope: str
    type_id: str
    team_id: str
    count: int
    type_name: str = ""

    @staticmethod
    def from_row(row: tuple[Any, ...]) -> Grant:
        scope, type_id, team_id, count, *rest = row
        type_name = str(rest[0]).strip() if rest and rest[0] else ""
        return Grant(str(scope), str(type_id), str(team_id), int(count), type_name)


def _as_grant(row: Grant | tuple[Any, ...]) -> Grant:
    return row if isinstance(row, Grant) else Grant.from_row(row)


@dataclass(frozen=True)
class ResolvedTeams:
    """Custody teams, and model teams, for this enrolment.

    A custody key is present when that custody named teams, including when none
    of them resolved. Absence means the account uses the firm-wide list. An
    empty list means the named teams are not on the book, and the firm-wide
    list must not fill the gap. A model key is the same shape. Model teams are
    added to the firm and custody grants; an unresolved model team is left off
    and does not replace them.
    """

    by_custody: Mapping[str, tuple[tuple[dict[str, int], ...], tuple[dict[str, int], ...]]] = field(
        default_factory=dict
    )
    by_model: Mapping[str, tuple[tuple[dict[str, int], ...], tuple[dict[str, int], ...]]] = field(
        default_factory=dict
    )

    def account_for(self, custody: str) -> list[dict[str, int]] | None:
        entry = self.by_custody.get(custody)
        if entry is None:
            return None
        return [dict(pair) for pair in entry[1]]

    def client_for(self, custody: str) -> list[dict[str, int]] | None:
        entry = self.by_custody.get(custody)
        if entry is None:
            return None
        return [dict(pair) for pair in entry[0]]

    def account_for_model(self, model_uid: str) -> list[dict[str, int]] | None:
        entry = self.by_model.get(model_uid)
        if entry is None:
            return None
        return [dict(pair) for pair in entry[1]]

    def client_for_model(self, model_uid: str) -> list[dict[str, int]] | None:
        entry = self.by_model.get(model_uid)
        if entry is None:
            return None
        return [dict(pair) for pair in entry[0]]


def representative_grant_day(counts: Sequence[tuple[date, int]]) -> date | None:
    """The latest day that still looks like the whole book.

    A thin extract can land after a full client/account file. Reading that
    newer day as the book makes a handful of accounts look like the firm's
    servicing pattern: the wrong adviser, and a custody team that is simply
    absent. A day with at least half the fullest recent count is the book.
    Among those, the latest.
    """
    usable = [(day, count) for day, count in counts if count > 0]
    if not usable:
        return None
    peak = max(count for _, count in usable)
    full = [day for day, count in usable if count * 2 >= peak]
    return max(full)


def selected_team_ids(value: Any) -> list[str]:
    """The tenant's DSR teams, as digit strings, in the order they were chosen."""
    if not isinstance(value, list):
        return []
    chosen: list[str] = []
    for item in value:
        text = str(item).strip()
        if text.isdigit() and text not in chosen:
            chosen.append(text)
    return chosen


def custody_dsr_lists(config: dict[str, Any]) -> dict[str, list[str]]:
    """DSR teams named on a custody, beside that side's optional subfirm."""
    raw = config.get("custody_dsrs")
    if not isinstance(raw, dict):
        return {}
    chosen: dict[str, list[str]] = {}
    for key in ("onshore", "offshore"):
        ids = selected_team_ids(raw.get(key))
        if ids:
            chosen[key] = ids
    return chosen


def model_dsr_lists(config: dict[str, Any]) -> dict[str, list[str]]:
    """DSR teams named on a model, keyed by that model's public id."""
    raw = config.get("model_dsrs")
    if not isinstance(raw, dict):
        return {}
    chosen: dict[str, list[str]] = {}
    for key, value in raw.items():
        uid = str(key).strip()
        if len(uid) != 12 or not uid.isalnum():
            continue
        ids = selected_team_ids(value)
        if ids:
            chosen[uid] = ids
    return chosen


def custody_name(account: dict[str, Any]) -> str:
    name = str(account.get("custody") or "onshore").strip().lower()
    return name if name in ("onshore", "offshore") else "onshore"


def _best_grant(rows: Iterable[Grant]) -> Grant | None:
    found = list(rows)
    if not found:
        return None
    return max(found, key=lambda row: (row.count, row.type_id))


def _type_on_scope(grants: Sequence[Grant], scope: str, type_name: str) -> str | None:
    """The relationship id this scope uses for a type of that name."""
    if not type_name:
        return None
    best = _best_grant(row for row in grants if row.scope == scope and row.type_name == type_name)
    if best is None or not best.type_id.isdigit():
        return None
    return best.type_id


def _pair(type_id: str, team_id: str) -> dict[str, int]:
    return {"servicingRelationshipId": int(type_id), "teamId": int(team_id)}


def configured_team_grants(
    dsr_ids: Any, grants: Sequence[Grant | tuple[Any, ...]]
) -> tuple[list[dict[str, int]], list[dict[str, int]]]:
    """The selected teams, each with the relationship id its scope already uses.

    A team granted only on accounts still goes on the client, under the client
    relationship of the same name when the firm has one, and under the account
    relationship otherwise. The reverse holds for a client-only team. A type id
    is never invented: a team the book has never granted is left off.
    """
    rows = [_as_grant(row) for row in grants]
    client: list[dict[str, int]] = []
    account: list[dict[str, int]] = []
    for team in selected_team_ids(dsr_ids):
        on_account = _best_grant(
            row for row in rows if row.scope == "account" and row.team_id == team
        )
        on_client = _best_grant(
            row for row in rows if row.scope == "client" and row.team_id == team
        )
        account_type = on_account.type_id if on_account and on_account.type_id.isdigit() else None
        client_type = on_client.type_id if on_client and on_client.type_id.isdigit() else None
        if client_type is None and on_account is not None:
            client_type = _type_on_scope(rows, "client", on_account.type_name) or account_type
        if account_type is None and on_client is not None:
            account_type = _type_on_scope(rows, "account", on_client.type_name) or client_type
        if client_type is not None:
            client.append(_pair(client_type, team))
        if account_type is not None:
            account.append(_pair(account_type, team))
    return client, account


def modal_employees(
    grants: Sequence[Grant | tuple[Any, ...]],
    *,
    scope: str = "account",
) -> tuple[list[dict[str, int]], dict[str, list[str]]]:
    """One scope's employee relationships, taken from the book.

    Each relationship goes to the employee who already holds it most often.
    The holder list is who already has that relationship. The journey's own
    adviser is a separate decision, made where the body is built.
    """
    grouped: dict[str, list[Grant]] = {}
    for row in (_as_grant(item) for item in grants):
        if row.scope != scope or not row.type_id.isdigit() or not row.team_id.isdigit():
            continue
        grouped.setdefault(row.type_id, []).append(row)
    pairs: list[dict[str, int]] = []
    holders: dict[str, list[str]] = {}
    for type_id in sorted(grouped):
        group = grouped[type_id]
        holders[type_id] = [row.team_id for row in group]
        top = max(row.count for row in group)
        winners = [row for row in group if row.count == top]
        if len(winners) != 1:
            continue
        pairs.append(
            {
                "servicingRelationshipId": int(type_id),
                "employeeEntityId": int(winners[0].team_id),
            }
        )
    return pairs, holders


def _modal_grant(grants: Sequence[Grant], scope: str) -> dict[str, int] | None:
    """The one servicing grant this firm already uses, when there is a single most-used pair."""
    scoped = [row for row in grants if row.scope == scope]
    if not scoped:
        return None
    top = max(row.count for row in scoped)
    winners = [row for row in scoped if row.count == top]
    if len(winners) != 1:
        return None
    winner = winners[0]
    if not winner.type_id.isdigit() or not winner.team_id.isdigit():
        return None
    return _pair(winner.type_id, winner.team_id)


def team_defaults(
    config: dict[str, Any], grants: Sequence[Grant | tuple[Any, ...]]
) -> tuple[dict[str, list[dict[str, int]]], ResolvedTeams]:
    """Firm-wide pairs to merge onto the in-memory config, and the custody resolutions.

    The firm-wide dict carries `client_team_ids` / `account_team_ids` only when
    this call produced them. Custody resolutions stay on `ResolvedTeams`.
    """
    rows = [_as_grant(row) for row in grants]
    selected = selected_team_ids(config.get("dsr_ids"))
    named = custody_dsr_lists(config)
    firm: dict[str, list[dict[str, int]]] = {}
    if selected:
        client_pairs, account_pairs = configured_team_grants(selected, rows)
        if client_pairs and not config.get("client_team_ids"):
            firm["client_team_ids"] = client_pairs
        if account_pairs and not config.get("account_team_ids"):
            firm["account_team_ids"] = account_pairs
    elif not named:
        if not config.get("client_team_ids"):
            pair = _modal_grant(rows, "client")
            if pair:
                firm["client_team_ids"] = [pair]
        if not config.get("account_team_ids"):
            pair = _modal_grant(rows, "account")
            if pair:
                firm["account_team_ids"] = [pair]
    by_custody: dict[str, tuple[tuple[dict[str, int], ...], tuple[dict[str, int], ...]]] = {}
    for custody, ids in named.items():
        client_pairs, account_pairs = configured_team_grants(ids, rows)
        by_custody[custody] = (tuple(client_pairs), tuple(account_pairs))
    by_model: dict[str, tuple[tuple[dict[str, int], ...], tuple[dict[str, int], ...]]] = {}
    for uid, ids in model_dsr_lists(config).items():
        client_pairs, account_pairs = configured_team_grants(ids, rows)
        by_model[uid] = (tuple(client_pairs), tuple(account_pairs))
    return firm, ResolvedTeams(by_custody, by_model)


def _merge(base: Any, extra: list[dict[str, int]]) -> list[dict[str, int]]:
    """Firm teams first, then the custody's, without repeating a pair."""
    merged: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for pair in list(base or []) + extra:
        key = (int(pair["servicingRelationshipId"]), int(pair["teamId"]))
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(pair))
    return merged


def account_teams(
    account: dict[str, Any], config: dict[str, Any], teams: ResolvedTeams | None
) -> Any:
    """Teams for this account.

    The firm's teams stay. A custody that named teams adds those, each under the
    account relationship the book already uses. A named team the book has never
    granted does not fall back to the firm's list. A model that named teams adds
    those as well, for an account that uses that model. An unresolved model team
    is left off and does not replace the grants already chosen.
    """
    firm = config.get("account_team_ids")
    if teams is None:
        base = firm
    else:
        named = teams.account_for(custody_name(account))
        if named is None:
            base = firm
        elif not named:
            base = named
        else:
            base = _merge(firm, named)
        extra = teams.account_for_model(str(account.get("model_uid") or "").strip()) or []
        if extra:
            return _merge(base, extra)
    return base


def client_teams(
    accounts: Sequence[dict[str, Any]], config: dict[str, Any], teams: ResolvedTeams | None
) -> Any:
    """Teams for a new client.

    The firm's teams are the client's grants. The custodies this enrolment opens
    add their own teams, under the client relationship of each. When only a
    custody named teams, those are the whole grant. A custody that named teams
    which did not resolve contributes nothing, and does not fall through to the
    most-used grant. A model this enrolment uses adds its teams on top.
    """
    firm_selected = bool(selected_team_ids(config.get("dsr_ids")))
    if teams is None:
        base = config.get("client_team_ids")
    else:
        chosen: list[dict[str, int]] = []
        seen: set[tuple[int, int]] = set()
        named = False
        for account in accounts:
            pairs = teams.client_for(custody_name(account))
            if pairs is None:
                continue
            named = True
            for pair in pairs:
                key = (int(pair["servicingRelationshipId"]), int(pair["teamId"]))
                if key in seen:
                    continue
                seen.add(key)
                chosen.append(pair)
        if firm_selected:
            base = _merge(config.get("client_team_ids"), chosen if named else [])
        elif named:
            base = chosen
        else:
            base = config.get("client_team_ids")
    extra: list[dict[str, int]] = []
    if teams is not None:
        for account in accounts:
            pairs = teams.client_for_model(str(account.get("model_uid") or "").strip())
            if pairs:
                extra.extend(pairs)
    if extra:
        return _merge(base, extra)
    return base
