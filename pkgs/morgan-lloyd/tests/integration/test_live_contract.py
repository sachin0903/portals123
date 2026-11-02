"""What the mocks cannot tell us: does Morgan Lloyd still behave the way this client assumes.

Every other test in this package asserts on a payload we built or a document we transcribed. Both
are necessary and neither can catch the failures that have actually cost this integration time,
because all of them were the counterparty disagreeing with our transcription of it:

* ``cam_updateschemevaluation`` — an action name that does not exist, wired into a scheduled sink,
  pinned by a mock that answered anything.
* ``cam_createbeneficiary`` vs ``cam_createbeneficiaries`` — the docs' name is not the org's.
* ``cam_schemerefs`` vs ``cam_schemes`` — two populations with ZERO overlap, so every scoped call
  was refused with a message that reads as "that scheme does not exist". Two rounds of "fixing" a
  model that had never produced a row.
* the action ENVELOPE — `cam_createcontact` answers `{@odata.context, message, response}` with the
  id nested, not a flat `contactid`. The contact was created every time and the id discarded.

So these are READ-ONLY probes of the vendor's own behaviour, and they are marked `integration`
(excluded from every gate — see the conftest). They are allowed to be SLOW and allowed to depend
on the network; what they must never be is silently skipped.

WHAT THEY DELIBERATELY DO NOT DO. They do not create anything by default. Dynamics has no delete,
so a create is permanent in a real firm's sandbox — the write probes are behind
`INTEGRATION_ALLOW_WRITES=1` and say so.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from morgan_lloyd._base import MorganLloydError
from morgan_lloyd.client import MorganLloydClient
from morgan_lloyd.manifest import operation, operations

pytestmark = pytest.mark.integration

PACKAGE = Path(__file__).resolve().parents[2]
ENDPOINTS = PACKAGE / "src" / "morgan_lloyd" / "endpoints"
SCRIPTS = PACKAGE / "scripts"


def _load(name: str) -> ModuleType:
    """One of the package's scripts, as a module. Same loader `test_catalogue_scripts.py` uses.

    `introspect_api` already knows how to fetch `$metadata` — a separate httpx client with
    `Accept: application/xml`, because the shared client defaults to JSON and Dataverse answers
    415 for an XML-only resource — and how to parse the action list out of it. Re-implementing
    either here would be a second answer to a question this package has already answered.
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


introspect = _load("introspect_api")


@pytest.fixture(scope="session")
async def org_metadata(settings: Any) -> str:
    """The org's `$metadata`, fetched ONCE.

    Session-scoped because the document is ~31 MB: fetching it per test would make this suite
    slow enough that nobody runs it, which is the failure mode that matters most for a suite
    already outside every gate.
    """
    return str(await introspect.fetch_metadata())


@pytest.fixture(scope="session")
def org_actions(org_metadata: str) -> dict[str, Any]:
    """Every ACTION the org declares, with its parameters and their types."""
    catalogue = introspect.parse_actions(org_metadata)
    assert catalogue, "no cam_* actions found — is this really a Dataverse $metadata document?"
    return dict(catalogue)


@pytest.fixture(scope="session")
def org_entity_sets(org_metadata: str) -> set[str]:
    """Every ENTITY SET the org declares.

    Needed because the manifest's `action` field is not always an action: an operation that is a
    plain OData read or a `PATCH cam_personalpolicies(<id>)` carries the entity set there. The
    first live run compared all of them against the action list and reported five entity sets —
    `cam_schemes`, `cam_personalpolicies`, `cam_contributions`,
    `cam_nominationofbeneficiaries`, `cam_payrequest` — as missing actions.
    """
    return set(re.findall(r'<EntitySet Name="([^"]+)"', org_metadata))


class TestAuthenticationAndReach:
    async def test_a_token_is_obtainable(self, api: MorganLloydClient) -> None:
        """The first thing that breaks, and the one a mock can never see.

        SEI restricts by source IP and Morgan Lloyd's Azure app is behind Conditional Access, so
        "the credentials are right" and "this machine may use them" are different questions.
        `_explain` exists to tell them apart in a message; this asserts the answer is yes.
        """
        contacts = await api.get_collection(
            "contacts", params={"$select": "contactid", "$top": "1"}
        )
        assert isinstance(contacts, list)

    async def test_a_refusal_is_explained_rather_than_raw(self, api: MorganLloydClient) -> None:
        """A 403 or an auth failure has to arrive as something an operator can act on. Asked of a
        table we know is forbidden, so it exercises the real refusal rather than a synthetic one."""
        # `MorganLloydError`, the BASE: `_base._request` wraps an HTTP failure into
        # `MorganLloydApiError`, so an httpx exception never escapes the client. The first live
        # run of this file asserted on `httpx.HTTPStatusError` and failed for that reason — the
        # client's contract is that it raises one of its own.
        with pytest.raises(MorganLloydError) as raised:
            await api.get_collection("cam_assetvaluations", params={"$top": "1"})
        # The privilege the principal is missing is NAMED, which is what makes a 403 actionable
        # rather than something to guess at.
        assert "403" in str(raised.value)


class TestEveryActionThisClientNamesExists:
    """`test_actions_exist.py` asserts this against the committed spec. This asserts the SPEC is
    still true — the org is what moves, and a spec regenerated months ago is a transcription."""

    @staticmethod
    def _named_actions() -> set[str]:
        """Every `call_action("…")` name in the endpoint modules, read as source."""
        found: set[str] = set()
        for module in ENDPOINTS.glob("*.py"):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "call_action"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    found.add(node.args[0].value)
        return found

    def test_the_org_metadata_names_every_action_we_post(self, org_actions: dict[str, Any]) -> None:
        """`$metadata` is the org's own statement of what exists and how it is spelled — the
        only complete list of this org's actions, and the artefact that established
        `cam_createinvestment` does not exist.

        Read through `introspect_api.parse_actions`, so this compares NAMES against a parsed
        catalogue rather than substring-searching 31 MB of XML: a bare `in document` would pass
        for an action name that appears only inside a description.
        """
        missing = sorted(name for name in self._named_actions() if name not in org_actions)
        assert not missing, (
            f"these actions are named by this client and absent from the org's $metadata: "
            f"{missing}. Dynamics answers an unknown unbound action with a generic failure, so "
            "the symptom would be a vendor error that reads as a permissions or payload problem."
        )


class TestTheActionsParametersAreTheAuthority:
    """**The entity's attributes are NOT the action's parameters, and only one of them decides
    what `build_contact` may send.**

    This distinction cost a wrong conclusion the first time these tests ran live. The org's
    `contact` ENTITY has `cam_additionalvulnerabilitynotes` (Memo) and `cam_contactvulnerability`
    (a multiselect) and has NO `cam_detailssupportingnotes`. Read on its own, that says the API
    layer sends a field the org does not have.

    The ACTION says the opposite, and the action is what we POST: `cam_createcontact` declares
    `cam_detailssupportingnotes`, `cam_vulnerabilitydrivers` and `cam_vulnerabilitycharacteristics`
    as String parameters, and declares NEITHER of the two entity attributes. The action maps its
    own parameters onto whatever it likes internally; a payload is checked against the parameter
    list.

    So this asserts against `$metadata`'s action definitions — the same artefact
    `introspect_api` already parses, and the only live authority on what a payload may contain.
    A name Dynamics does not recognise is IGNORED, not refused, so nothing else would say.
    """

    #: Every field `app/integrations/morgan_lloyd/contacts.py::build_contact` can emit.
    #:
    #: Restated here rather than imported: `apps/api` is not a dependency of this package (the
    #: dependency runs the other way). The API's own contract test pins this list against
    #: `vendor_manifest.json`; this pins the same names against the ORG, which is what catches a
    #: manifest that has drifted from the deployment.
    CONTACT_FIELDS = (
        "as_contactstatus",
        "cam_title",
        "firstname",
        "middlename",
        "lastname",
        "emailaddress1",
        "cam_nationality",
        "gendercode",
        "cam_ninumber",
        "address1_line1",
        "address1_line2",
        "address1_city",
        "address1_county",
        "address1_postalcode",
        "cam_birthdate",
        "familystatuscode",
        "cam_retirementageint",
        "cam_employmentstatus",
        "cam_sourceofwealth",
        "cam_sourceofwealthstring",
        "cam_annualearnings",
        "cam_taxrelief",
        "cam_externalclientid",
        "cam_previouslytakenflexiaccessbenefits",
        "cam_potentiallyvulnerable",
        "cam_detailssupportingnotes",
        "cam_vulnerabilitydrivers",
        "cam_vulnerabilitycharacteristics",
        "telephone1",
        "mobilephone",
        "cam_financialadviser_Account",
        "cam_FinancialAdviserContact",
    )

    #: What the API sends each as. `Edm.Int32` and a JSON string are different requests, and the
    #: difference surfaces only as a vendor 400 on a live call.
    EXPECTED_TYPES = {
        "cam_previouslytakenflexiaccessbenefits": "Int32",
        "cam_taxrelief": "Int32",
        "cam_employmentstatus": "Int32",
        "cam_sourceofwealth": "Int32",
        "gendercode": "Int32",
        "familystatuscode": "Int32",
        "cam_title": "Int32",
        "as_contactstatus": "Int32",
        "cam_retirementageint": "Int32",
        # A STRING even though the entity attribute is a Boolean — the vendor's own note, and the
        # reason `build_contact` sends "True"/"False". Confirmed against the action, which is the
        # surface that matters.
        "cam_potentiallyvulnerable": "String",
        # The multiselects: one comma-separated string of option numbers, not an array.
        "cam_vulnerabilitydrivers": "String",
        "cam_vulnerabilitycharacteristics": "String",
        "cam_detailssupportingnotes": "String",
        "cam_externalclientid": "String",
    }

    def test_every_field_we_send_is_a_declared_parameter(self, org_actions: dict[str, Any]) -> None:
        declared = {p["name"] for p in org_actions["cam_createcontact"]["params"]}
        unknown = sorted(set(self.CONTACT_FIELDS) - declared)
        assert not unknown, (
            f"cam_createcontact does not declare {unknown}. Dynamics IGNORES a parameter it does "
            "not recognise, so these change nothing and the call still reports success — which is "
            "exactly how `cam_additionalvulnerabilitynotes` discarded every vulnerable client's "
            "notes."
        )

    def test_the_field_we_used_to_send_is_still_not_a_parameter(
        self, org_actions: dict[str, Any]
    ) -> None:
        """The specific regression, asserted from the org rather than from the manifest.

        `cam_additionalvulnerabilitynotes` is a real ATTRIBUTE on the contact entity, which is
        why it looked plausible for as long as it did — it is simply not something this action
        accepts.
        """
        declared = {p["name"] for p in org_actions["cam_createcontact"]["params"]}
        assert "cam_additionalvulnerabilitynotes" not in declared
        assert "cam_contactvulnerability" not in declared

    @pytest.mark.parametrize(("field", "expected"), sorted(EXPECTED_TYPES.items()))
    def test_the_declared_type_is_what_we_send(
        self, org_actions: dict[str, Any], field: str, expected: str
    ) -> None:
        declared = {p["name"]: p["type"] for p in org_actions["cam_createcontact"]["params"]}
        assert declared.get(field) == expected, (
            f"{field} is declared {declared.get(field)!r} and the API sends it as if it were "
            f"{expected!r} — a JSON bool or string against an Edm.Int32 is a vendor 400 that "
            "names nothing useful."
        )


class TestTheOptionSetValuesStillExist:
    """The VALUES behind the int parameters, read from the entity's picklists.

    The entity is the right surface for this one — an action parameter typed `Int32` carries no
    option set of its own, and the values it accepts are the attribute's. `gendercode` was
    verified by hand once (exactly {1 Male, 2 Female}, which is why a client recorded as anything
    else is refused at the boundary rather than coerced); this does it for each of them per run.
    """

    #: Attributes that ARE entity picklists. The two vulnerability multiselects are deliberately
    #: absent: they exist only as action PARAMETERS on this org, so there is no attribute to read
    #: an option set from and the manifest's own list is the only description of their values.
    PICKLISTS = (
        "gendercode",
        "familystatuscode",
        "cam_employmentstatus",
        "cam_sourceofwealth",
        "cam_taxrelief",
    )

    @staticmethod
    async def _org_options(api: MorganLloydClient, attribute: str) -> set[int]:
        path = (
            "EntityDefinitions(LogicalName='contact')/Attributes"
            f"(LogicalName='{attribute}')/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
            "?$select=LogicalName&$expand=OptionSet($select=Options)"
        )
        body = await api.get(path)
        options = (body.get("OptionSet") or {}).get("Options") or []
        return {int(option["Value"]) for option in options if "Value" in option}

    @pytest.mark.parametrize("attribute", PICKLISTS)
    async def test_the_org_still_accepts_every_documented_value(
        self, api: MorganLloydClient, attribute: str
    ) -> None:
        """The manifest's values must be a SUBSET of the org's.

        Subset rather than equal: the org gaining a member is a vendor addition we do not use
        yet, which is not a failure. The org LOSING one is — it means we send a value that no
        longer exists, and Dynamics reports that as a generic failure.
        """
        found = operation("cam_createcontact")
        assert found is not None
        field = found.field(attribute)
        if field is None or not field.options:
            pytest.skip(f"{attribute} documents no option set to check")

        org = await self._org_options(api, attribute)
        assert org, f"{attribute} returned no options — is it still a picklist on this org?"
        ours = {option.value for option in field.options}
        assert ours <= org, (
            f"{attribute}: the manifest documents {sorted(ours - org)} which the org no longer "
            "has — a payload carrying one of those is refused with a message naming nothing."
        )


class TestTheEnvelopeIsStillAnEnvelope:
    """The shape `_created_id`, `_scheme_reference` and `_policy_echo` all parse.

    Checked on a READ action rather than a create, which is the whole point: the envelope is a
    property of how the org answers a custom action, not of what the action does, so it can be
    established without leaving a permanent record behind.
    """

    async def test_a_custom_action_answers_an_envelope_not_a_flat_body(
        self, api: MorganLloydClient
    ) -> None:
        try:
            body = await api.call_action("cam_getscheme", {"schemeRef": "MLSIPP/00001"})
        except MorganLloydError as exc:
            # "Scheme not found" is a perfectly good answer for this assertion: it proves the
            # action exists, takes the parameter, and reports through the envelope. What it must
            # NOT be is an action-not-found, which is the `cam_updateschemevaluation` failure.
            assert "not found" in str(exc).lower() or "no " in str(exc).lower()
            return
        assert isinstance(body, dict)
        assert "@odata.context" in body or "response" in body or "message" in body, (
            f"the action answered a shape none of our parsers expect: {sorted(body)}"
        )


class TestTheSchemeReferenceTrap:
    """`cam_schemerefs` and `cam_schemes` are different populations, and the overlap is ZERO.

    The reference SERIES (`QSIPP/00652`, one per SIPP product line) against the scheme itself
    (`MLSIPP/01417`). Every scoped action takes the latter, and four models landed empty for a
    run of releases because they were fed the former. This is live-verifiable and cost two rounds
    of fixing a model that had never produced a row, so it is worth re-asserting rather than
    remembering.
    """

    async def test_the_series_is_one_row_per_product_line_not_the_scheme_population(
        self, api: MorganLloydClient
    ) -> None:
        """This asserted ZERO OVERLAP between the two sets, and that was wrong — not about the
        conclusion, about the invariant. **`cam_schemerefs.cam_name` is a live COUNTER.**

        Seven rows, one per SIPP product line, the MLSIPP row created in 2022 — and its name reads
        `MLSIPP/02114` today because that is the scheme we issued this morning. It read
        `MLSIPP/02113` yesterday. So the series row for a line always equals the newest scheme on
        that line, and an overlap of exactly one per prefix is the NORMAL state: the old assertion
        would have gone red the first time anybody created a scheme, which is a false alarm about
        a fact that has not changed.

        What actually matters for `_SCHEME_SET = "cam_schemes"` is unaffected, and is what is
        asserted here instead: the series is a handful of counters, not the book. Reading it to
        enumerate schemes gives seven rows whatever the firm holds — which is the shape of the bug
        that left four models empty for a run of releases.
        """
        series = [
            str(row.get("cam_name") or "")
            for row in await api.get_collection(
                "cam_schemerefs", params={"$select": "cam_name", "$top": "200"}
            )
        ]
        schemes = [
            str(row.get("cam_name") or "")
            for row in await api.get_collection(
                "cam_schemes", params={"$select": "cam_name", "$top": "200"}
            )
        ]
        series = [name for name in series if name]
        schemes = [name for name in schemes if name]
        if not series or not schemes:
            pytest.skip("one of the two collections is empty on this org")

        def line(reference: str) -> str:
            return reference.split("/", 1)[0]

        # One row per product line: the property that makes it a series register rather than a
        # population. Two rows for one line would mean it is something else.
        assert len({line(name) for name in series}) == len(series), (
            f"cam_schemerefs has more than one row for some product line: {sorted(series)}"
        )
        # And it is not the scheme population — the assertion `_SCHEME_SET` rests on. Any overlap
        # is a counter having caught up with its newest scheme, never the two being interchangeable.
        assert set(schemes) - set(series), (
            "every scheme is also in cam_schemerefs, so the two sets may have merged — "
            "`_SCHEME_SET = 'cam_schemes'` assumes they have not."
        )
        for shared in set(series) & set(schemes):
            same_line = [name for name in schemes if line(name) == line(shared)]
            assert shared == max(same_line), (
                f"{shared} is in both sets but is not the newest scheme on its line "
                f"({sorted(same_line)}) — the counter explanation does not hold."
            )

    async def test_an_unknown_scheme_answers_EMPTY_rather_than_refusing(
        self, api: MorganLloydClient
    ) -> None:
        """**The wrong-identifier bug did not look like an error, and that is the whole problem.**

        This file first asserted that feeding a scoped action a reference from the SERIES was
        REFUSED. The org says otherwise: `cam_getpensioncontributionapplications` answers a
        series reference with `{"contributionApplications": [], "message": "No Contribution
        Applications found for Scheme HISIPP/00645"}` — a successful call, an empty list, and a
        message. Which is exactly why four models landed empty for a run of releases and it read
        as "this firm has no schemes" rather than as a wrong identifier.

        So the valuable assertion is the shape of the EMPTINESS, not a refusal that never comes.
        """
        series = await api.get_collection(
            "cam_schemerefs", params={"$select": "cam_name", "$top": "1"}
        )
        if not series:
            pytest.skip("this org lists no scheme reference series")
        reference = str(series[0].get("cam_name") or "")

        answer = await api.call_action(
            "cam_getpensioncontributionapplications", {"schemeReference": reference}
        )
        assert answer.get("contributionApplications") == []
        message = str(answer.get("message") or "")
        assert reference in message, (
            "the vendor's empty answer no longer names the reference it was given — which is the "
            "only thing that would let somebody notice they sent the wrong kind"
        )

    async def test_the_empty_message_still_matches_the_syncs_own_test_for_it(
        self, api: MorganLloydClient
    ) -> None:
        """`services/morgan-lloyd/models/raw_scoped.py::_is_empty_result` anchors on a LEADING
        "no", because several of these actions report "nothing here" by raising and counting that
        as a failure produced seven false failures a run.

        The live message is "No Contribution Applications found for Scheme …". Asserted against
        the sync's own predicate rather than a copy of it, so a vendor rewording that broke the
        anchor shows up here rather than as a run that reports failures it should not.
        """
        schemes = await api.get_collection(
            "cam_schemes", params={"$select": "cam_name", "$top": "1"}
        )
        if not schemes:
            pytest.skip("this org lists no schemes")
        answer = await api.call_action(
            "cam_getpensioncontributionapplications",
            {"schemeReference": str(schemes[0].get("cam_name") or "")},
        )
        message = str(answer.get("message") or "")
        if not message:
            pytest.skip("this scheme has contribution applications, so there is no empty message")
        assert message.strip().lower().startswith("no "), (
            f"the vendor's empty-result message is now {message!r}, which `_is_empty_result`'s "
            "leading-'no' anchor would not match — an empty answer would start being counted as "
            "a failure"
        )


class TestWhatOurServicePrincipalMayRead:
    """The 85 forbidden `cam_*` tables are a permissions request to Clifton, not a code gap.

    `docs/morgan-lloyd/endpoint-coverage.md` records the probe: 38 return rows, 39 empty, 85 give
    403 — and the forbidden set is most of the valuation and risk data. Waiting on a vendor to
    grant something is a state nothing currently observes, so this turns it into a test that
    CHANGES when they do.
    """

    #: What we depend on. A 403 here is a live outage of a real feature.
    REQUIRED = ("contacts", "cam_schemes", "cam_personalpolicies", "cam_contributions")

    #: What the coverage doc records as forbidden. Not a failure — an expected refusal, reported
    #: when it lifts so a granted permission is noticed rather than waited for.
    EXPECTED_FORBIDDEN = (
        "cam_assetvaluations",
        "cam_policyvaluations",
        "cam_cashbalances",
        "cam_attitudetorisks",
    )

    @pytest.mark.parametrize("collection", REQUIRED)
    async def test_the_collections_we_depend_on_are_readable(
        self, api: MorganLloydClient, collection: str
    ) -> None:
        rows = await api.get_collection(collection, params={"$top": "1"})
        assert isinstance(rows, list)

    @pytest.mark.parametrize("collection", EXPECTED_FORBIDDEN)
    async def test_a_lifted_permission_is_reported(
        self, api: MorganLloydClient, collection: str
    ) -> None:
        """Passes while the table is still forbidden, and FAILS the day it becomes readable.

        An inverted test, deliberately. The alternative is that a permission Clifton grants sits
        unnoticed for months while `sink_valuations` keeps reporting that it cannot value anything
        — and `cam_policyvaluations` becoming readable is exactly what would let a valuation push
        be verified instead of trusted.
        """
        try:
            await api.get_collection(collection, params={"$top": "1"})
        except MorganLloydError:
            return
        pytest.fail(
            f"{collection} is READABLE now — our service principal has been granted access. "
            "That is good news and a code change: see docs/morgan-lloyd/endpoint-coverage.md §4, "
            "and remove it from EXPECTED_FORBIDDEN."
        )


class TestTheDocumentedContractStillDescribesTheOrg:
    #: Documented by the vendor, absent from THIS org, and used by nothing.
    #:
    #: `cam_payrequest` — the Benefits section's "Pay Request", `POST /cam_payrequest` taking a
    #: single `payRequest` field. Found by the first live run of this suite. It is neither an
    #: action nor an entity set in the dev org's `$metadata`, and no client code names it, so
    #: nothing is broken today — but a payload built from the published documentation would be
    #: refused with a message that reads as a permissions or payload problem, which is the exact
    #: shape of the `cam_updateschemevaluation` incident.
    #:
    #: Recorded rather than silenced: a red run for a known, non-blocking vendor gap is noise
    #: that gets ignored, and a deleted assertion is a fact nobody re-checks. The companion test
    #: below fails if it ever appears.
    KNOWN_ABSENT = frozenset({"cam_payrequest"})

    def test_the_manifest_and_the_org_agree_on_the_action_list(
        self, org_actions: dict[str, Any], org_entity_sets: set[str]
    ) -> None:
        """A documented operation whose action the org does not have is a call nobody can make.

        Checked against ACTIONS **and** ENTITY SETS, because the manifest's `action` field holds
        whichever of the two the operation targets — a `PATCH cam_personalpolicies(<id>)` is an
        entity write, not an unbound action, and comparing it against the action list alone
        reported five perfectly good entity sets as missing.

        Reported as one assertion listing all of them rather than parametrised, so a
        documentation refresh that invents ten actions reads as one problem.
        """
        from morgan_lloyd.manifest import ACTION_ALIASES

        known = set(org_actions) | org_entity_sets
        absent = {
            op.action
            for op in operations()
            if op.action.startswith("cam_")
            # An alias IS the reconciliation — `ACTION_ALIASES` maps a documented name onto the
            # action the client really posts, and the client is authoritative there.
            and ACTION_ALIASES.get(op.action, op.action) not in known
        }
        unexpected = sorted(absent - self.KNOWN_ABSENT)
        assert not unexpected, (
            f"vendor_manifest.json documents action(s) the org does not have: {unexpected}. "
            "`manifest.ACTION_ALIASES` is where a documented name that differs from the real one "
            "is reconciled — the client is authoritative, so add the mapping there. If it is "
            "genuinely absent from this org, record it in KNOWN_ABSENT with what it is."
        )

    def test_a_documented_action_arriving_is_reported(
        self, org_actions: dict[str, Any], org_entity_sets: set[str]
    ) -> None:
        """The mirror of the assertion above, and inverted like the permissions probe: this FAILS
        the day a known-absent operation appears in the org.

        A vendor enabling something they already document is not a thing anyone goes looking for,
        and the alternative is that it stays in `KNOWN_ABSENT` for ever describing a state that
        stopped being true.
        """
        arrived = sorted(self.KNOWN_ABSENT & (set(org_actions) | org_entity_sets))
        assert not arrived, (
            f"{arrived} now exists in the org — it was documented and uncallable. Remove it from "
            "KNOWN_ABSENT; it may be worth wiring up."
        )


class TestTheWritePaths:
    """Everything here creates something PERMANENT. Off unless asked for.

    There is no cleanup and there cannot be: Dynamics has no delete for these entities, which is
    the same fact that makes `retry='manual'` the default for the vendor operations and makes
    `sync_client` check `platform.links` before every step. A record made here stays in Clifton's
    sandbox, so each one is named so a human can see what it was.
    """

    def _marker(self) -> str:
        import datetime as dt

        return f"ZZ-INTEGRATION-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}"

    async def test_a_contact_is_created_once_and_its_id_read_back(
        self, api: MorganLloydClient, writes_allowed: None
    ) -> None:
        """The bug this exists for: the contact WAS created every time and the id discarded,
        because the parser read a flat `contactid` where the envelope nests it. A mock cannot
        catch it — only the org's real answer can.

        **It used to mint a fresh contact per run, and could therefore only pass once.** Morgan
        Lloyd enforces NI-number uniqueness across contacts (`0x80040224 The ni number is already
        used for an existing contact`), so the second run of a probe with a fixed NINO fails on
        the vendor's rule rather than on anything this asserts — and giving it a per-run NINO
        would put a permanent new person in a real firm's sandbox on every invocation, in a system
        with no delete. So it reuses the ANCHOR, like everything else here: created on the first
        run against a fresh org, found on every run after it.

        Which means the envelope assertion only has a create to inspect on the first run. That is
        stated rather than hidden — the alternative is a test that quietly asserts nothing while
        appearing to cover the scar it was written for.
        """
        from morgan_lloyd.manifest import operation as documented

        from . import anchors

        found = await anchors.find_client(api)
        if found is not None:
            guid = str(found["contactid"])
            assert _GUID.fullmatch(guid), f"the anchor client's id is not a GUID: {guid!r}"
            pytest.skip(
                f"the anchor client already exists ({anchors.CLIENT_MARKER}); the ENVELOPE shape "
                "is only observable on a create. Point this at a fresh org to exercise it."
            )

        adviser = await anchors.ensure_adviser(api, self._firm_id())
        payload = anchors.client_payload(self._firm_id(), adviser)
        contract = documented("cam_createcontact")
        assert contract is not None
        unknown = sorted(set(payload) - {f.name for f in contract.fields})
        assert not unknown, f"this probe sends undocumented field(s): {unknown}"

        answer = await api.create_contact(payload)
        # The assertion that matters: the id is findable in whatever shape came back.
        guid = _created_id_of(answer)
        assert guid, (
            f"created a contact and could not read its id out of {sorted(answer)} — this is the "
            "scar: the record exists and nothing links to it."
        )

    def _firm_id(self) -> str:
        import os

        firm = (os.environ.get("MORGAN_LLOYD_ADVISER_FIRM_ID") or "").strip()
        if not firm:
            pytest.skip("MORGAN_LLOYD_ADVISER_FIRM_ID is not set")
        return firm


_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _created_id_of(response: dict[str, Any]) -> str | None:
    """The API's own parser, re-implemented here in one line rather than imported.

    `apps/api` is not a dependency of this package (the dependency runs the other way), so the
    envelope rule is asserted on its SHAPE. If this and
    `app/integrations/morgan_lloyd/contacts.py::_created_id` ever disagree, the API's is the one
    that matters and this is what would have told you the shape changed.
    """
    for candidate in (
        *(v for v in response.values() if isinstance(v, str)),
        *(
            v
            for inner in response.values()
            if isinstance(inner, dict)
            for v in inner.values()
            if isinstance(v, str)
        ),
        json.dumps(response),
    ):
        found = _GUID.search(candidate)
        if found:
            return found.group(0)
    return None
