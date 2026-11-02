"""Per-tenant provider enablement (platform.tenant_platforms).

A tenant enables a platform provider with the filter that selects THEIR slice of the
whole-firm core data — the fan-out predicate (docs/storage.md §9): Morgan Lloyd filters
by the connection's **Microsoft tenant id** (one Dynamics/Azure tenant per firm; the sync
stamps every record with it), Seccl by node ids, SEI by firm/subfirm/DSR ids. Config
shapes are validated here, per provider. `platform.providers` names the known
upstream ids (the admin dropdown).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: The custodians and scheme administrators this platform integrates with, in the ONE spelling the
#: estate uses (0131). It was three — `morgan_lloyd` here and in the database, `morgan-lloyd` in
#: the URL, the service directory, the moon id and the docker service, and `ml` in the warehouse —
#: and Morgan Lloyd was the only provider where any two differed, so it was the only one a missing
#: conversion could break. It did: a slug reaching `set_source` refused to register the Microsoft
#: tenant that enabling a firm depends on. The hyphen won because it is the form a person types
#: and the one the filesystem already used. Mirrored by `platform.provider_code`.
#:
#: `graphene` is a member of the enum (0179) and is NOT in this tuple: it is the
#: ``platform.transactions.source_system`` of an outbound pending row, not a platform a firm
#: enables. `docs/transaction-status.md`.
PROVIDERS = ("morgan-lloyd", "seccl", "sei")

# Braces and case are both accepted on the way in and normalised by the caller that sends
# it; the vendor refuses a braced GUID (`Invalid Format for cam_financialadviser_Account`).
_GUID = re.compile(r"\{?[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\}?")

# ------------------------------------------------------------------------------------------------
# How each provider PRESENTS — the metadata the admin console needs and an overview payload cannot
# carry, because it describes the counterparty rather than a run.
#
# HERE, because `PROVIDERS` is already the source of truth and adding a custodian should be one
# edit. It was seven: the admin app carried `LIVE_PROVIDERS`, `LIVE_META`, three fixture `meta`
# blocks, `PROVIDER_DEFS`, `PROVIDER_NAMES`, `PLATFORM_NAMES` and its own `PROVIDERS` tuple — all
# hard-coded to the same three slugs, none derived from the API, and already drifted: Seccl was
# described three different ways depending on which page you were on.
#
# THERE IS NO `slug` ANY MORE (0131). It used to be the URL form, declared rather than computed
# precisely because leaving the substitution implicit is how `provider_schema` went wrong — a rule
# that held for two providers and not the third. Declaring it did not help: four of nineteen admin
# routes forgot to cross the bridge, and the one provider the bridge existed for was the one that
# broke. The key IS the URL form now, so there is nothing to convert and nothing to forget.
PROVIDER_META: dict[str, dict[str, Any]] = {
    "morgan-lloyd": {
        "name": "Morgan Lloyd",
        "role": "scheme_administrator",
        "description": (
            "SIPP scheme administrator — daily Dynamics CRM sync into the core warehouse, "
            "sliced per firm."
        ),
        "transports": ["api"],
        "source_label": "Microsoft (Dynamics) tenants — one per firm",
        "kpis": [["Contacts", "platform.clients"], ["Transfers", "platform.transfers"]],
    },
    "seccl": {
        "name": "Seccl",
        "role": "custodian",
        "description": (
            "Custodian — scheduled and cursor-based pulls, with webhook streams carrying the "
            "intraday movement."
        ),
        "transports": ["api", "webhooks"],
        "source_label": "Seccl nodes (a firm can span several)",
        "kpis": [["Clients", "platform.clients"], ["Transactions", "platform.transactions"]],
    },
    "sei": {
        "name": "SEI",
        "role": "custodian",
        "description": (
            "Custodian — nightly PGP-encrypted SFTP drop, plus a REST surface for cash and "
            "account instructions."
        ),
        "transports": ["sftp", "api"],
        "source_label": "Firm",
        "kpis": [["Clients", "platform.clients"], ["Accounts", "platform.accounts"]],
    },
}


def code_named(name: str) -> str | None:
    """Canonical provider code for a registry display name, or none when it is unknown."""
    return next(
        (code for code, entry in PROVIDER_META.items() if entry.get("name") == name),
        None,
    )


def administrators() -> tuple[str, ...]:
    """The providers that ADMINISTER a scheme rather than custody the assets.

    The distinction is already in `PROVIDER_META['role']` and it is the one that decides whether
    a provider's cash figure is new information. A CUSTODIAN's cash is the account's own
    uninvested balance, which every portal already shows from the lake's positions — counting it
    again from `platform.valuations` would double it. A SCHEME ADMINISTRATOR's is the scheme's own
    bank account, held somewhere else entirely, and is the figure a client cannot otherwise see
    (0117).

    Derived rather than hardcoded to `morgan_lloyd`: a second administrator would otherwise be
    silently excluded from every cash breakdown, which reads as "that firm has no cash there".
    """
    return tuple(
        provider
        for provider, entry in PROVIDER_META.items()
        if entry.get("role") == "scheme_administrator"
    )


def enabled_custodians(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Custodians this firm has switched on, as ``{code, name}``.

    A scheme administrator is not one. A listing offers a custodian filter only when
    more than one of these is enabled — a firm on a single custodian has nothing to choose.
    """
    known = set(custodians())
    return [
        {"code": str(row["provider"]), "name": str(row["name"])}
        for row in rows
        if row.get("enabled") and row.get("provider") in known
    ]


def custodians() -> tuple[str, ...]:
    """The providers that CUSTODY the assets — the ones whose slice holds positions.

    The other half of `administrators()`, and what the display path narrows to. Every holdings,
    allocation, chart and cash read goes to `tenant_{uid}.positions`, which only a custodian's
    chain writes: an account administered by Morgan Lloyd carries a `platform.links` row like any
    other, and reading a positions relation its project does not publish would raise
    `HoldingsUnavailable` and degrade the page to the dev extract.

    Derived from `PROVIDER_META['role']` for the same reason `administrators()` is: hardcoding it
    to `seccl` is precisely what left a SEI firm's portal empty.
    """
    return tuple(
        provider for provider, entry in PROVIDER_META.items() if entry.get("role") == "custodian"
    )


def morgan_lloyd_custodian() -> str:
    """The custodian whose pension schemes Morgan Lloyd administers.

    Seccl administers its own SIPPs, so a drawdown session is only meaningful here. The
    code comes from the registry's display name, so a router does not spell a custodian.
    """
    code = code_named("SEI")
    if code is None:
        raise RuntimeError("SEI is not a registered provider")
    return code


def meta(provider: str) -> dict[str, Any]:
    """How this provider presents, or an honestly unnamed shape for one we have no metadata for.

    NEVER another provider's metadata. An unlisted provider used to fall back to Morgan Lloyd's
    on the UI side, so every SEI page carried Morgan Lloyd's name and description above SEI's own
    data and its own "Send to…" list — which is the confusion that gets an instruction sent to the
    wrong custodian. Unnamed beats misnamed.
    """
    found = PROVIDER_META.get(provider)
    if found is not None:
        return dict(found)
    return {
        "name": provider.replace("-", " "),
        "role": "custodian",
        "description": "No display metadata for this platform yet.",
        "transports": ["api"],
        "source_label": "sources",
        "kpis": [],
    }


def provider_schema(provider: str) -> str:
    """The catalogue schema a provider's data lives under: ml | seccl | sei.

    Moved here from the deleted `app/fanout.py`, which is where it happened to be written rather
    than where it belongs — this module already owns `PROVIDERS` and the platform registry, and the
    mapping is a fact about a provider, not about a projection step.

    It is NOT the bare provider name for Morgan Lloyd, and the two coincide for the other two, which
    is what made it dangerous: `materialise._read_conformed` read the bare name and so read schema
    `morgan_lloyd` when ML's data is in `ml` — the tenant's whole slice reported as unavailable, and
    only ML affected.

    DERIVED, with no map at all (0134). It carried `{"morgan-lloyd": "ml"}` until the catalogue
    schema was renamed to match the provider code, and a map is a thing to forget: this exact one
    was forgotten once, when `materialise._read_conformed` read the bare provider name and so read
    schema `morgan_lloyd` while ML's data was in `ml` — the tenant's whole slice reported as
    unavailable, which reads as "this firm has no data here", and only ML was affected because
    only ML's two names differed.

    The substitution that remains is not a second NAME, it is an ENCODING: `-` is not legal in an
    unquoted SQL identifier, so the schema is the code with the hyphen replaced. `seccl` and `sei`
    contain none and pass through unchanged.
    """
    return provider.replace("-", "_")


# interlace promotes its default environment UNPREFIXED (`platform.clients`), and that default is
# production — which is the only environment the API reads. There is deliberately no setting for
# this: a non-production catalogue would be a code change, not a config flag, and the previous
# DATALAKE_ENV knob only ever produced a `dev__` prefix nothing had promoted to.


def promoted_relation(model_name: str) -> str:
    """Where a model's promoted view lives in the core catalogue. A model with no schema
    prefix lands in `main`."""
    schema, _, table = model_name.rpartition(".")
    return f"{schema or 'main'}.{table}"


# provider -> (required list-ish keys, optional keys). Kept deliberately simple; the
# custodian mapping work (docs/data-architecture.md) refines these into rule tables later.
_CONFIG_KEYS: dict[str, tuple[set[str], set[str]]] = {
    # Microsoft tenant GUIDs (one per firm), plus the vendor reference values THAT CONNECTION
    # issues. The four used to live on `platform.providers` as a separate "named source" row that
    # only Morgan Lloyd ever had, and its join to this table was 1:1 for that reason (0133).
    # OPTIONAL because the deployment settings remain the documented fallback for the firm the
    # vendor issues one pair for — `sync.py` refuses a HALF-configured product rather than topping
    # it up, which is the rule that makes optional safe here.
    "morgan-lloyd": (
        {"tenant_ids"},
        {"firm_id", "adviser_contact_id", "scheme_type", "sipp_type"},
    ),
    "seccl": ({"node_ids"}, set()),
    # SEI narrows in three nested steps: the firm (exactly one — a firm is the
    # tenant's SEI identity), then optionally its subfirms and its DSR teams.
    # Both narrowing keys are LISTS: a firm can hand a tenant several servicing
    # teams (Blackfinch has 434 DSR teams) and several subfirms (onshore +
    # offshore). Omitting one means "no narrowing on that axis" — the whole firm.
    #
    # The two contribution keys are NOT slice narrowing — they are the SEI reference codes
    # `app/integrations/sei/cash_receipts.py` reads from this same column to lodge an Anticipated
    # Cash Receipt. They were absent from this allowlist while being read from the column and
    # documented as living here (docs/contributions.md §4), so `set_provider()` answered
    # "unknown config keys for sei: ['contribution_description_code', …]" and the only way to
    # configure contribution lodgement was to write the row by hand.
    "sei": (
        {"firm_id"},
        {
            "subfirm_ids",
            "dsr_ids",
            # The templates a new client is opened with, one per client type. Not a
            # slice filter: the product's enrolment template opens the account. The
            # platforms card owns the keys (`_FORM_KEYS`): leaving a control blank
            # clears that choice. `client_template_id` is the individual template,
            # kept so a connection saved before the split still opens a person.
            # A firm with exactly one client template is still filled from that
            # for both types when a side is absent.
            "client_template_id",
            "client_template_individual_id",
            "client_template_organisation_id",
            "contribution_description_code",
            "contribution_treatment_codes",
            # Write map: Graphene `onshore`/`offshore` → the SEI subfirm a new account
            # opens in. Distinct from `subfirm_ids` (the read slice). Discovered from
            # warehouse labels (`… - Onshore` / `… - Offshore`), then attached.
            "custody_orgs",
            # Optional DSR teams for a new account opened in that custody, beside the
            # optional subfirm. Absent on a side means the firm-wide `dsr_ids`. Not a
            # slice filter: the card owns it (`_FORM_KEYS`), so a blank control clears it.
            "custody_dsrs",
            # Optional DSR teams per model, keyed by the model's public id. Added to
            # the firm and custody grants when a new client or account uses that model.
            # The card owns it, so a save with no model teams clears it.
            "model_dsrs",
            # The custodian's collection bank account — where a client pays money IN.
            # Shown in the account's payment drawer (the "Custodian bank details" block).
            # SEI publishes no per-firm pay-in account, so it is firm configuration, not
            # derived. The card owns the three keys, so a blank control clears them.
            "pay_in_account_name",
            "pay_in_sort_code",
            "pay_in_account_number",
        },
    ),
}

#: Graphene custody locations. The product's `allowed_custodies` and the account's
#: `custody` use this pair; a custodian may honour only a subset (Seccl is onshore only).
CUSTODIES = ("onshore", "offshore")
_CUSTODY_LABEL = (
    ("offshore", re.compile(r"(?:^|[\s\-–—])offshore\s*$", re.IGNORECASE)),
    ("onshore", re.compile(r"(?:^|[\s\-–—])onshore\s*$", re.IGNORECASE)),
)

# Keys that must be non-empty lists of non-blank strings when present.
_LIST_KEYS = ("tenant_ids", "node_ids", "dsr_ids", "subfirm_ids")
# Keys that must be a single non-blank string.
_SCALAR_KEYS = (
    "firm_id",
    "contribution_description_code",
    "client_template_id",
    "client_template_individual_id",
    "client_template_organisation_id",
    # SEI custodian pay-in account. Present ⇒ non-blank; a blank control omits the key
    # (and `_FORM_KEYS` then clears it) rather than storing an empty string.
    "pay_in_account_name",
    "pay_in_sort_code",
    "pay_in_account_number",
)
#: Keys that must be a GUID, and keys that must be a whole number — Morgan Lloyd's connection
#: values (0133). The shapes matter because the failure of a typo is SILENCE: `scheme_typ` stores
#: cleanly, resolves to nothing, and leaves scheme creation refusing for a value an operator can
#: see on screen. `firm_id` is deliberately NOT here: SEI uses that key for its slice firm, which
#: is not a GUID, and one name means two things across two providers.
#: The keys that say WHICH SLICE of a provider a firm gets. They are REPLACED wholesale on a
#: write, because removing a narrowing axis is expressed by its key going absent — a SEI firm
#: narrowed to two subfirms and then widened back to the whole firm sends no `subfirm_ids` at all.
_SLICE_KEYS: dict[str, frozenset[str]] = {
    "morgan-lloyd": frozenset({"tenant_ids"}),
    "seccl": frozenset({"node_ids"}),
    "sei": frozenset({"firm_id", "subfirm_ids", "dsr_ids"}),
}

#: Keys the enablement form owns that are not slice filters. A blank control omits the
#: key, and that omission clears the stored value. Contribution codes are in neither
#: set: the card does not carry them, so a save must leave them. `custody_orgs` is
#: pruned against `subfirm_ids` rather than listed here, because an omitted map still
#: has to be narrowed when the subfirm slice shrinks.
_FORM_KEYS: dict[str, frozenset[str]] = {
    "sei": frozenset(
        {
            "client_template_id",
            "client_template_individual_id",
            "client_template_organisation_id",
            "custody_dsrs",
            # `model_dsrs` is NOT here: a model's DSR teams are edited on the model itself now
            # (the catalogue's Models editor), not the platform card, so a card save must PRESERVE
            # them the way it preserves the contribution codes. `set_model_dsrs` is the writer.
            "pay_in_account_name",
            "pay_in_sort_code",
            "pay_in_account_number",
        }
    ),
}

#: Keys whose value must be a GUID, BY PROVIDER — because one key name means two things.
#:
#: `firm_id` is Morgan Lloyd's adviser-firm GUID and ALSO SEI's slice firm, which is a short
#: numeric code. Keying the check on the name alone therefore could not express it, and the note
#: here used to record that omission as deliberate — but the `_SOURCE_CONFIG_KEYS` this replaced
#: WAS per provider (`{"morgan_lloyd": {"firm_id": "guid", …}}`) and its test asserted the
#: refusal, so the shape was lost rather than traded away.
#:
#: It matters because `platform_actions` maps ONE value under three vendor field names from
#: `firm_id` — `cam_financialadviser_Account`, Create Adviser's `accountId` and Create
#: Investment's `provider` — so a malformed one prefills all three: the vendor answers "Invalid
#: Format for cam_financialadviser_Account", or accepts a well-formed-but-wrong GUID as an
#: `accounts` reference and every later `cam_createscheme` 403s.
_GUID_KEYS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "morgan-lloyd": ("firm_id", "adviser_contact_id"),
}

#: Keys that are a GUID whatever the provider.
_GUID_KEYS = ("adviser_contact_id",)
_INT_KEYS = ("scheme_type", "sipp_type")

#: Keys holding a mapping, and the members each allows. Declared here rather than imported from
#: `cash_receipts` to keep this module free of integration imports; `tests/test_providers.py` pins
#: the two together, because the failure of a typo is SILENCE — a misspelt member stores cleanly,
#: resolves to nothing, and leaves the 422 naming a code an operator can see on screen.
_MAPPING_KEYS: dict[str, frozenset[str]] = {
    # cash_receipts.TREATMENT_CODE_FOR's values: the treatment code is what encodes
    # "personal vs employer" at SEI, so it is per contribution category, not one per firm.
    "contribution_treatment_codes": frozenset({"personal", "employer", "third_party"}),
    "custody_orgs": frozenset(CUSTODIES),
}


def slice_config_keys(provider: str) -> tuple[str, ...]:
    """The config keys naming the upstream ids a firm's slice is keyed on.

    The REQUIRED keys only: SEI's optional `subfirm_ids`/`dsr_ids` narrow within a firm rather
    than adding slices, so counting by them would double-count.
    """
    required, _ = _CONFIG_KEYS.get(provider, (set(), set()))
    return tuple(sorted(required))


def replaced_config_keys(provider: str) -> frozenset[str]:
    """Keys a write replaces. Slice filters, plus the fields the enablement form owns.

    Everything else on the stored config is preserved. That is what keeps a card save
    from deleting the contribution codes the card does not carry.
    """
    return _SLICE_KEYS.get(provider, frozenset()) | _FORM_KEYS.get(provider, frozenset())


def validate_config(provider: str, config: dict[str, Any]) -> None:
    """Shape-check the enablement filter. Raises ValueError with a clear message."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider '{provider}'")
    required, optional = _CONFIG_KEYS[provider]
    unknown = set(config) - required - optional
    if unknown:
        raise ValueError(f"unknown config keys for {provider}: {sorted(unknown)}")
    missing = required - set(config)
    if missing:
        raise ValueError(f"missing config keys for {provider}: {sorted(missing)}")
    for key in _LIST_KEYS:
        if key in config and (
            not isinstance(config[key], list)
            or not config[key]
            or not all(isinstance(v, str) and v.strip() for v in config[key])
        ):
            raise ValueError(f"{key} must be a non-empty list of strings")
    for key in _SCALAR_KEYS:
        if key in config and not (isinstance(config[key], str) and config[key].strip()):
            raise ValueError(f"{key} must be a non-empty string")
    guid_keys = {*_GUID_KEYS, *_GUID_KEYS_BY_PROVIDER.get(provider, ())}
    for key in sorted(guid_keys):
        if key in config and config[key] not in (None, ""):
            if not _GUID.fullmatch(str(config[key]).strip()):
                raise ValueError(
                    f"{key} must be a GUID. Note it is NOT the Microsoft tenant id — that is the "
                    "Azure directory, this is a Dynamics record inside it."
                )
    for key in _INT_KEYS:
        if key in config and config[key] not in (None, ""):
            number = config[key]
            if isinstance(number, bool) or not isinstance(number, int):
                raise ValueError(f"{key} must be a whole number (the vendor's option-set member)")
    for key, members in _MAPPING_KEYS.items():
        if key not in config:
            continue
        value = config[key]
        if not isinstance(value, dict) or not value:
            raise ValueError(f"{key} must be a non-empty object")
        stray = set(value) - members
        if stray:
            raise ValueError(f"unknown {key} entries: {sorted(stray)}; known: {sorted(members)}")
        if not all(isinstance(v, str) and v.strip() for v in value.values()):
            raise ValueError(f"{key} values must be non-empty strings")
    if provider == "sei" and "custody_dsrs" in config:
        _validate_custody_dsrs(config["custody_dsrs"])
    if provider == "sei" and "model_dsrs" in config:
        _validate_model_dsrs(config["model_dsrs"])
    if provider == "sei" and "custody_orgs" in config and "subfirm_ids" in config:
        allowed = {str(item).strip() for item in config["subfirm_ids"]}
        outside = [
            str(item).strip()
            for item in config["custody_orgs"].values()
            if str(item).strip() not in allowed
        ]
        if outside:
            raise ValueError("custody_orgs must name a selected subfirm")


def _validate_custody_dsrs(value: Any) -> None:
    """Onshore/offshore → the DSR teams a new account in that custody is opened with.

    Each side is optional, and either side may be set without a subfirm. An empty
    object is not a choice, so it is refused the same way an empty `dsr_ids` is.
    """
    if not isinstance(value, dict) or not value:
        raise ValueError("custody_dsrs must be a non-empty object")
    stray = set(value) - set(CUSTODIES)
    if stray:
        raise ValueError(
            f"unknown custody_dsrs entries: {sorted(stray)}; known: {sorted(CUSTODIES)}"
        )
    for key in CUSTODIES:
        if key not in value:
            continue
        teams = value[key]
        if (
            not isinstance(teams, list)
            or not teams
            or not all(isinstance(item, str) and item.strip() for item in teams)
        ):
            raise ValueError(f"custody_dsrs.{key} must be a non-empty list of strings")


_MODEL_UID = re.compile(r"^[A-Za-z0-9]{12}$")


def _validate_model_dsrs(value: Any) -> None:
    """Model public id → the DSR teams a new client and account on that model receive.

    An empty object is not a choice. A key that is not a model id is refused, so a
    typo does not store a grant enrolment will never look up.
    """
    if not isinstance(value, dict) or not value:
        raise ValueError("model_dsrs must be a non-empty object")
    for key, teams in value.items():
        if not isinstance(key, str) or _MODEL_UID.fullmatch(key) is None:
            raise ValueError("model_dsrs keys must be model ids")
        if (
            not isinstance(teams, list)
            or not teams
            or not all(isinstance(item, str) and item.strip() for item in teams)
        ):
            raise ValueError(f"model_dsrs.{key} must be a non-empty list of strings")


def custody_from_label(label: str | None) -> str | None:
    """Onshore/offshore from an SEI subfirm name (`Select Platform - Onshore`).

    Suffix-anchored so a name that merely mentions the word elsewhere does not match, and
    offshore is tried first only as documentation — the two suffixes do not overlap.
    """
    text = (label or "").strip()
    if not text:
        return None
    for custody, pattern in _CUSTODY_LABEL:
        if pattern.search(text):
            return custody
    return None


def infer_custody_orgs(
    subfirms: Sequence[tuple[str, str]],
    selected_ids: Sequence[str] | None = None,
) -> dict[str, str]:
    """Attach Graphene custody to a subfirm id from warehouse labels.

    `selected_ids` is the tenant's read slice; empty/None means the whole firm (every
    given subfirm). First labelled match wins per custody. A sole unlabelled subfirm
    becomes onshore — that is the default open, not a guess at offshore. Two unlabelled
    subfirms cannot be told apart, so the map stays empty until an operator picks.
    """
    selected = [str(item).strip() for item in (selected_ids or []) if str(item).strip()]
    pool = (
        set(selected) if selected else {str(sid).strip() for sid, _ in subfirms if str(sid).strip()}
    )
    labelled: dict[str, str] = {}
    for sid, label in subfirms:
        sid = str(sid).strip()
        if not sid or sid not in pool:
            continue
        custody = custody_from_label(label)
        if custody and custody not in labelled:
            labelled[custody] = sid
    if labelled:
        return labelled
    if len(pool) == 1:
        return {"onshore": next(iter(pool))}
    return {}


def _custody_orgs(config: dict[str, Any] | None) -> dict[str, str]:
    raw = (config or {}).get("custody_orgs") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in CUSTODIES:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def openable_custodies(
    provider: str | None, config: dict[str, Any] | None = None
) -> frozenset[str]:
    """Custody locations this custodian can actually open an account in.

    Seccl has no offshore org. SEI honours the attached `custody_orgs` map; a firm
    with no subfirms (Blackfinch 3102) has an empty map and both Graphene locations
    remain openable — there is simply no org to send. An unmapped account (no
    custodian yet) keeps both, and the product's `allowed_custodies` still binds.
    """
    if provider == "seccl":
        return frozenset({"onshore"})
    if provider == "sei":
        mapped = frozenset(_custody_orgs(config))
        return mapped if mapped else frozenset(CUSTODIES)
    return frozenset(CUSTODIES)


def account_org_id(
    provider: str | None, config: dict[str, Any] | None, custody: str | None
) -> str | None:
    """The SEI subfirm a new account with this custody opens in, or none.

    The client still sits at `firm_id` (`residingOrgId`). Putting the subfirm on the
    client is vendor error 2345; putting the firm on the account is 2048. Seccl has
    no equivalent — its node is the tenant identity, not a custody split.
    """
    if provider != "sei":
        return None
    key = (custody or "onshore").strip().lower()
    return _custody_orgs(config).get(key)


def _prune_custody_orgs(config: dict[str, Any]) -> None:
    """Drop write-map entries that no longer name a selected subfirm."""
    orgs = _custody_orgs(config)
    if not orgs:
        config.pop("custody_orgs", None)
        return
    allowed = None
    if "subfirm_ids" in config:
        allowed = {str(item).strip() for item in config["subfirm_ids"]}
    pruned = {key: value for key, value in orgs.items() if allowed is None or value in allowed}
    if pruned:
        config["custody_orgs"] = pruned
    else:
        config.pop("custody_orgs", None)


async def list_for_tenant(session: AsyncSession, tenant_id: int) -> list[dict[str, Any]]:
    """This firm's enablements, each carrying the provider's display name.

    The NAME travels with the row because the alternative is a lookup table in whichever surface
    renders it — and there were five of those. Nobody outside the database calls it
    `morgan_lloyd`, and title-casing the key gives "Sei".
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, provider, config, enabled, created_at, "
                    # COALESCE for the expand phase: a row last written before 0079 has only
                    # the old column. The wire name stays `last_fanout_at` until the portals
                    # stop reading it, so this is a rename of the STORAGE, not of the contract.
                    "  COALESCE(last_merged_at, last_fanout_at) AS last_fanout_at "
                    "FROM platform.tenant_platforms WHERE tenant_id = :t ORDER BY provider"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    return [{**dict(r), "name": meta(str(r["provider"]))["name"]} for r in rows]


async def set_model_dsrs(
    session: AsyncSession, tenant_id: int, model_uid: str, dsr_ids: Sequence[str]
) -> None:
    """Set (or clear) the SEI DSR teams for one model, editing ``config.model_dsrs[model_uid]`` on
    the firm's SEI row in place.

    A targeted jsonb merge, not ``set_provider``: that replaces the slice keys wholesale, so a
    one-model edit would have to resend ``firm_id``/``subfirm_ids`` or clear them. This touches
    only the one model's entry and leaves the rest of the config — and every other model — alone.
    The editing moved to the catalogue's Models editor; the platform card no longer carries it.
    """
    if not re.fullmatch(r"[A-Za-z0-9]{12}", model_uid):
        raise ValueError(f"{model_uid!r} is not a model public id.")
    teams = [str(item).strip() for item in dsr_ids if str(item).strip()]
    if teams:
        await session.execute(
            text(
                "UPDATE platform.tenant_platforms "
                "SET config = COALESCE(config, '{}'::jsonb) || jsonb_build_object("
                "  'model_dsrs', COALESCE(config -> 'model_dsrs', '{}'::jsonb) "
                "    || jsonb_build_object(CAST(:uid AS text), CAST(:ids AS jsonb))) "
                "WHERE tenant_id = :t AND provider = 'sei'"
            ),
            {"t": tenant_id, "uid": model_uid, "ids": json.dumps(teams)},
        )
    else:
        await session.execute(
            text(
                "UPDATE platform.tenant_platforms "
                "SET config = COALESCE(config, '{}'::jsonb) "
                "  #- ARRAY['model_dsrs', CAST(:uid AS text)] "
                "WHERE tenant_id = :t AND provider = 'sei'"
            ),
            {"t": tenant_id, "uid": model_uid},
        )


async def set_provider(
    session: AsyncSession,
    tenant_id: int,
    provider: str,
    *,
    config: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    """Enable/update/disable a provider for a tenant (upsert). Validates the config.

    MERGES the config; it used to REPLACE it, and 0140 is what made that destructive. Folding
    `platform.providers` into this table moved the vendor reference values onto this very column —
    Morgan Lloyd's `firm_id`/`adviser_contact_id`/`scheme_type`/`sipp_type`, SEI's contribution
    description and treatment codes — beside the slice filter. The only UI writer builds
    `{tenant_ids}` or `{firm_id, subfirm_ids, dsr_ids}` and nothing else, so an operator adding a
    DSR team or toggling a provider deleted them: every later contribution failed to lodge at SEI
    with a 422 naming a code that was configured yesterday, and `platform_actions.defaults_for`
    stopped prefilling `cam_createwrapper`'s provider GUID. With the "Register Morgan Lloyd
    Tenant" sheet gone there was no way to write them back short of SQL. The `set_source` this
    replaced had `config = COALESCE(...)` and a docstring saying "a form that does not carry the
    config must not silently empty it" — the rule survived its implementation by one migration.

    The SLICE keys and the form-owned keys are replaced wholesale
    (`replaced_config_keys`). Removing a narrowing axis is said by its key going
    ABSENT — a firm widened back from two subfirms to the whole firm sends no
    `subfirm_ids`, and a merge would keep the old list and silently keep narrowing.
    The client template and the custody DSR lists are not narrowing axes, but the
    platforms card owns them, so a blank control clears them the same way. Every
    other key is preserved, which is what stops the card deleting the contribution
    codes it does not carry.

    Enabling a scheme administrator also maps every SIPP on the shelf to that provider as
    `role='scheme_administrator'` (`attach_scheme_administrators`). A SIPP custodied at SEI
    and administered by Morgan Lloyd cannot declare that until both mappings exist, and
    waiting for an operator to tag each product by hand left `scheme_administrator` null on
    every onboarding SIPP. A TPPENSION is not mapped: Morgan Lloyd does not run it.

    Enabling SEI maps GIA, ISA, JISA and SIPP as `role='custodian'`
    (`attach_custodians`). The product list and the journey both require that role, and
    the nightly merge only creates the row once an account already exists at SEI, so a
    firm that had just been enabled opened every account with no custodian and every
    action skipped. A third-party bond or pension is not mapped: SEI does not open it.
    """
    validate_config(provider, config)
    stored = (
        await session.execute(
            text(
                "SELECT config FROM platform.tenant_platforms "
                " WHERE tenant_id = :t AND provider = :p"
            ),
            {"t": tenant_id, "p": provider},
        )
    ).scalar_one_or_none() or {}
    stored = dict(stored)
    replaced = replaced_config_keys(provider)
    merged = {key: value for key, value in stored.items() if key not in replaced}
    merged.update(config)
    # A firm switch leaves the previous firm's subfirm ids in `custody_orgs` unless the
    # form attached a new map. Those ids are not this firm's, and enrolment would send
    # them as the account org.
    if (
        provider == "sei"
        and config.get("firm_id") != stored.get("firm_id")
        and "custody_orgs" not in config
    ):
        merged.pop("custody_orgs", None)
    _prune_custody_orgs(merged)
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO platform.tenant_platforms "
                    "  (tenant_id, provider, config, enabled) "
                    "VALUES (:t, :p, :c, :e) "
                    "ON CONFLICT (tenant_id, provider) DO UPDATE SET "
                    "  config = EXCLUDED.config, enabled = EXCLUDED.enabled "
                    "RETURNING id, provider, config, enabled, last_fanout_at"
                ),
                {"t": tenant_id, "p": provider, "c": json.dumps(merged), "e": enabled},
            )
        )
        .mappings()
        .one()
    )
    if enabled and provider in administrators():
        await attach_scheme_administrators(session, tenant_id)
    if enabled and provider == "sei":
        await attach_custodians(session, tenant_id)
    return dict(row)


# Tenant connection keys copied onto a product the first time a scheme administrator is
# attached. The product stores the vendor's option-set names.
_ML_CONFIG_TO_PRODUCT = (
    ("scheme_type", "ml_scheme_type"),
    ("sipp_type", "ml_sipp_type"),
)

# Wrappers a scheme administrator on this platform actually runs. TPPENSION and SSAS are
# pensions too, and attaching them named Morgan Lloyd on products it does not administer.
ADMINISTERED_PRODUCTS = ("SIPP",)


def _scheme_admin_product_data(provider: str, config: dict[str, Any]) -> dict[str, Any]:
    """Per-product settings a first attach can copy from the tenant's connection config.

    Only Morgan Lloyd has any today (`ml_scheme_type` / `ml_sipp_type`). Unreadable values are
    omitted rather than stored: a typo here would otherwise become the pairing `cam_createscheme`
    sends, and the vendor cannot tell a wrong valid option-set member from a right one.
    """
    if provider != "morgan-lloyd":
        return {}
    data: dict[str, Any] = {}
    for src, dest in _ML_CONFIG_TO_PRODUCT:
        raw = config.get(src)
        if raw in (None, ""):
            continue
        try:
            data[dest] = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
    return data


def administered(product_type: str | None) -> bool:
    """Whether this wrapper is one a scheme administrator on this platform actually runs."""
    return str(product_type or "").upper() in ADMINISTERED_PRODUCTS


# Wrappers a custodian on this platform opens before any account exists at that
# custodian. Third-party bonds and pensions are held elsewhere; tagging them would
# offer SEI on a product the journey must not send there.
CUSTODIED_PRODUCTS = ("GIA", "ISA", "JISA", "SIPP")


def custodied(product_type: str | None) -> bool:
    """Whether enabling SEI should connect this wrapper as a custodian."""
    return str(product_type or "").upper() in CUSTODIED_PRODUCTS


async def attach_custodians(
    session: AsyncSession, tenant_id: int, product_id: int | None = None
) -> int:
    """Link GIA, ISA, JISA and SIPP to SEI when the firm has SEI enabled.

    Called when SEI is enabled (`set_provider`), when a product is written
    (`catalogue.upsert_product`), and from onboarding setup so a firm that enabled
    SEI before this attach existed still gets the mapping on the journey that
    needs it. The migration is the one-shot for a shelf nobody saves again.

    The nightly merge creates these rows only from accounts already at SEI, and a
    template save used to insert the row with a null role. The journey and the
    action runner both require `role='custodian'`, so either gap opens the account
    with no custodian and the action is skipped.

    Idempotent: unique `(product_id, provider)`. An existing row keeps its external
    id, enrolment template, settings and enabled flag. A null role is filled.
    Disabling SEI does not delete the mapping — an operator removes one from the
    catalogue. An account that still has no custodian, and whose product now names
    exactly one enabled custodian, takes that custodian. An account that already
    names one, or whose product names two, is left as it is.
    """
    if "sei" not in custodians():
        return 0
    enabled = (
        await session.execute(
            text(
                "SELECT 1 FROM platform.tenant_platforms "
                "WHERE tenant_id = :t AND enabled AND provider = 'sei'"
            ),
            {"t": tenant_id},
        )
    ).scalar_one_or_none()
    if enabled is None:
        return 0
    result = await session.execute(
        text(
            # external_id = the wrapper itself (GIA/ISA/JISA/SIPP). It is the custodian's own
            # reference for the product and does not change between firms, so there is no reason to
            # leave it blank until the nightly merge discovers one from an account that may never
            # exist — the shelf would show a bare "—" against an enabled custodian. coalesce keeps a
            # value the merge or an operator already set; only a null is filled.
            "INSERT INTO platform.product_platforms "
            "  (tenant_id, product_id, provider, external_id, role, enabled) "
            "SELECT p.tenant_id, p.id, CAST('sei' AS platform.provider_code), "
            "       p.product_type::text, :role, true "
            "FROM platform.products p "
            "WHERE p.tenant_id = :t "
            "  AND (CAST(:product_id AS int) IS NULL "
            "       OR p.id = CAST(:product_id AS int)) "
            "  AND upper(p.product_type::text) = ANY(:wrappers) "
            "ON CONFLICT (product_id, provider) DO UPDATE SET "
            "  role = coalesce(platform.product_platforms.role, EXCLUDED.role), "
            "  external_id = "
            "    coalesce(platform.product_platforms.external_id, EXCLUDED.external_id), "
            "  updated_at = now() "
            "WHERE platform.product_platforms.role IS NULL "
            "   OR platform.product_platforms.external_id IS NULL"
        ),
        {
            "t": tenant_id,
            "product_id": product_id,
            "wrappers": list(CUSTODIED_PRODUCTS),
            "role": meta("sei").get("role") or "custodian",
        },
    )
    attached = int(getattr(result, "rowcount", 0) or 0)
    await session.execute(
        text(
            "UPDATE platform.accounts a "
            "   SET custodian = chosen.provider, updated_at = now() "
            "  FROM ( "
            "    SELECT a2.id, (array_agg(pp.provider))[1] AS provider "
            "    FROM platform.accounts a2 "
            "    JOIN platform.product_platforms pp "
            "      ON pp.product_id = a2.product_id AND pp.tenant_id = a2.tenant_id "
            "     AND pp.enabled AND pp.role = 'custodian' "
            "    JOIN platform.tenant_platforms tp "
            "      ON tp.tenant_id = a2.tenant_id AND tp.provider = pp.provider "
            "     AND tp.enabled "
            "    WHERE a2.tenant_id = :t AND a2.custodian IS NULL "
            "      AND (CAST(:product_id AS int) IS NULL "
            "           OR a2.product_id = CAST(:product_id AS int)) "
            "      AND NOT EXISTS ( "
            "        SELECT 1 FROM platform.links l "
            "        WHERE l.tenant_id = a2.tenant_id AND l.entity_type = 'account' "
            "          AND l.entity_id = a2.id AND l.link_type = 'primary' "
            "          AND l.provider::text = ANY(:custodians) "
            "      ) "
            "    GROUP BY a2.id "
            "    HAVING count(DISTINCT pp.provider) = 1 "
            "  ) chosen "
            " WHERE a.id = chosen.id"
        ),
        {
            "t": tenant_id,
            "product_id": product_id,
            "custodians": list(custodians()),
        },
    )
    return attached


async def attach_scheme_administrators(
    session: AsyncSession, tenant_id: int, product_id: int | None = None
) -> int:
    """Link SIPP shelf items to every enabled scheme administrator for this firm.

    Called when an administrator is enabled (`set_provider`) and when a product is written
    (`catalogue.upsert_product`). Also called from onboarding `setup_account` so a firm that
    enabled Morgan Lloyd before this attach existed still gets the mapping on the journey
    that needs it.

    SIPP only. `is_pension` is true for TPPENSION and SSAS as well, and using it as the
    other half of the filter named Morgan Lloyd as administrator on wrappers it does not
    run. A later call also DROPS those mappings (the one-off that tagged them is expand-only
    and would otherwise leave Drawdown lit on a third-party pension).

    Idempotent: unique `(product_id, provider)`. Existing per-product scheme-type pairings are
    preserved; a first attach copies the tenant config's `scheme_type`/`sipp_type` into `data`
    so `cam_createscheme` has a pairing without a second admin visit. Disabling the provider
    does not delete a SIPP mapping — expand-only; an operator removes one from the catalogue.
    """
    admins = list(administrators())
    if not admins:
        return 0
    enabled = (
        (
            await session.execute(
                text(
                    "SELECT provider::text AS provider, config "
                    "FROM platform.tenant_platforms "
                    "WHERE tenant_id = :t AND enabled AND provider = ANY(:admins)"
                ),
                {"t": tenant_id, "admins": admins},
            )
        )
        .mappings()
        .all()
    )
    pensions = list(ADMINISTERED_PRODUCTS)
    scope = {
        "t": tenant_id,
        "product_id": product_id,
        "pensions": pensions,
    }
    # Drop mappings this administrator should not hold, even when the provider is currently
    # disabled: the previous OR-is_pension attach left TPPENSION tagged, and disable is
    # expand-only so those rows would otherwise sit on the shelf forever.
    for provider in admins:
        await session.execute(
            text(
                "DELETE FROM platform.product_platforms pp "
                "USING platform.products p "
                "WHERE pp.product_id = p.id "
                "  AND pp.tenant_id = :t "
                "  AND pp.provider = CAST(:provider AS platform.provider_code) "
                "  AND (CAST(:product_id AS int) IS NULL "
                "       OR p.id = CAST(:product_id AS int)) "
                "  AND upper(p.product_type::text) <> ALL(:pensions)"
            ),
            {**scope, "provider": provider},
        )
    attached = 0
    for row in enabled:
        provider = str(row["provider"])
        data = _scheme_admin_product_data(provider, dict(row["config"] or {}))
        result = await session.execute(
            text(
                # external_id = the wrapper itself (SIPP) — the administrator's own reference for
                # the product, stable across firms, so the shelf shows it rather than a bare "—".
                # coalesce keeps a value already set; only a null is filled.
                "INSERT INTO platform.product_platforms "
                "  (tenant_id, product_id, provider, external_id, role, enabled, data) "
                "SELECT p.tenant_id, p.id, CAST(:provider AS platform.provider_code), "
                "       p.product_type::text, :role, true, CAST(:data AS jsonb) "
                "FROM platform.products p "
                "WHERE p.tenant_id = :t "
                "  AND (CAST(:product_id AS int) IS NULL "
                "       OR p.id = CAST(:product_id AS int)) "
                "  AND upper(p.product_type::text) = ANY(:pensions) "
                "ON CONFLICT (product_id, provider) DO UPDATE SET "
                "  role = EXCLUDED.role, "
                "  enabled = true, "
                "  external_id = "
                "    coalesce(platform.product_platforms.external_id, EXCLUDED.external_id), "
                "  data = CASE "
                "    WHEN platform.product_platforms.data IS NULL "
                "      OR platform.product_platforms.data = '{}'::jsonb "
                "    THEN EXCLUDED.data "
                "    ELSE platform.product_platforms.data "
                "  END, "
                "  updated_at = now()"
            ),
            {
                **scope,
                "provider": provider,
                "role": meta(provider).get("role") or "scheme_administrator",
                "data": json.dumps(data),
            },
        )
        attached += int(getattr(result, "rowcount", 0) or 0)
    return attached


# `enabled_tenants` used to sit here: the fan-out's tenant iterator, reading enabled pairings
# whose tenant had ACTIVE storage. The fan-out is deleted and the pipeline asks that question
# for itself now, in the process that acts on the answer — `book_sink.tenants.enabled()`, as
# `book_writer`, at project load. The rule it encoded (no active bucket => the firm registers
# no models at all) moved to that function's own test rather than dying with this one.


async def resolve_source_config(
    session: AsyncSession, provider: str, tenant_id: int
) -> dict[str, Any]:
    """The vendor reference values this firm's connection carries.

    ONE READ SINCE 0133. It used to walk two tables — the firm's `tenant_platforms.config` for the
    external id, then `platform.providers.config` for that connection's values — and it REFUSED
    rather than picking when a firm held several, because the wrong choice sends one firm's
    adviser-firm GUID with another firm's client, which the vendor accepts as well-formed.
    `platform.providers` held Morgan Lloyd rows and nothing else, deliberately, and ML's
    connection is one Microsoft tenant per firm, so the join was always 1:1 for the only provider
    that used it and the refusal guarded a shape nothing could act on. The values live on the
    firm's own row now, so there is nothing left to disambiguate.

    An unconfigured connection answers `{}`, which the caller falls back to env for.
    """
    row = (
        await session.execute(
            text(
                "SELECT config FROM platform.tenant_platforms "
                "WHERE tenant_id = :t AND provider = CAST(:p AS platform.provider_code)"
            ),
            {"t": tenant_id, "p": provider},
        )
    ).scalar_one_or_none()
    return vendor_values(provider, dict(row or {}))


def vendor_values(provider: str, config: dict[str, Any]) -> dict[str, Any]:
    """The vendor reference values out of a config a caller ALREADY HOLDS.

    Split out so a caller that has just read the row does not read it again — the platform
    console's action-defaults handler called `_firm_platform_config` and then
    `resolve_source_config` one line apart, which since 0133 folded the registry in are the same
    `SELECT config FROM platform.tenant_platforms` with the second's answer a strict subset of the
    first's.

    ONLY the vendor values, never the slice filter. `_CONFIG_KEYS`' optional set is the same
    allowlist `validate_config` writes through, so a caller reading this cannot be handed
    `tenant_ids` and mistake it for a reference the vendor issued.
    """
    _, optional = _CONFIG_KEYS.get(provider, (set(), set()))
    return {key: value for key, value in config.items() if key in optional}
