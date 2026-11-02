"""What the generated models cannot tell us: does Seccl still answer the shape we inferred.

**Seccl publishes no schema.** `pkgs/seccl/README.md` says it plainly: the API is documented as a
Postman collection, 377 requests with 205 example bodies and no schema anywhere in the 8.2 MB
export. Every model in this package is INFERRED from those examples — "required" means "present in
every example", which the README itself calls decent evidence and not a guarantee.

That makes a live probe worth more here than for a vendor with a spec. The unit suite validates
488 documented payloads against models built from those same payloads, which is a closed loop: it
proves the merge was faithful to the collection, and cannot prove the collection is faithful to
the API. Only calling it can.

READ-ONLY by default. Seccl does have delete, so a write here is recoverable — the gate exists to
avoid littering staging rather than because a mistake is permanent (the Morgan Lloyd suite's is
the other way round, and says so).
"""

from __future__ import annotations

from typing import Any

import pytest

from seccl.client import SecclClient

pytestmark = pytest.mark.integration


class TestAuthenticationAndReach:
    async def test_a_token_is_obtainable_and_the_firm_resolves(self, api: SecclClient) -> None:
        """The first thing that breaks. Both auth shapes end here, so this is the one test that
        says "the credentials in this environment work against this firm"."""
        models = await api.models()
        assert isinstance(models, list)

    async def test_an_unknown_record_is_a_clean_refusal(self, api: SecclClient) -> None:
        """Not a crash and not an empty dict: a caller has to be able to tell "no such client"
        from "the call failed", and `get_record` is what every read goes through."""
        from seccl._base import SecclApiError

        with pytest.raises((SecclApiError, Exception)) as raised:
            await api.client("ZZZZZZZZZZ")
        assert str(raised.value)


class TestTheInferredShapesSurviveContactWithTheApi:
    """The generated models against what the API actually returns.

    This is the check the closed loop cannot make. A field the collection's examples all carried
    and the API omits is a `required` we inferred wrongly — which for a READ model means a
    validation error on live data, and for a WRITE model means we would have refused a legitimate
    call.
    """

    #: Fields the generator inferred as REQUIRED that the live API does not always send.
    #:
    #: **This is the README's own caveat, disproved.** `pkgs/seccl/README.md`: "required means
    #: present in every example", which it calls decent evidence and not a guarantee. Of 288 live
    #: client records on staging, 222 fail validation on these eight paths alone — so "present in
    #: every saved example" turns out to be a statement about which examples Seccl chose to save.
    #:
    #: Harmless TODAY and worth knowing anyway: nothing validates a live read through these models
    #: (`seccl_sync.records` is authoritative for the sync, and `platform_contracts` deliberately
    #: does not enforce Seccl requiredness for exactly this reason). It would become a 500 the day
    #: a read path called `Client.model_validate`.
    #:
    #: Recorded rather than silenced, and paths rather than a blanket skip: a NEW divergence still
    #: fails, which is the point of running this at all. The real repair is `build_schemas.py`'s
    #: inference rule, which is a change to 136 generated classes and its own piece of work.
    KNOWN_OVER_REQUIRED = frozenset(
        {
            "updateHistory.updateAction",
            "updateHistory.updateReason",
            "updateHistory.updatedFields",
            "updateHistory.updateData",
            "updateHistory.updateData.toData.bankDetails",
            "updateHistory.updateData.fromData.bankDetails",
            "amlData.checkDate",
            "amlData.amlEvidenceSeen",
        }
    )

    #: TYPE divergences, recorded ONE AT A TIME with both observed shapes.
    #:
    #: Deliberately not a blanket allowance like the missing-field set above: a wrong TYPE is a
    #: worse finding than an absent optional, so each one is named with what the vendor actually
    #: sends and anything else still fails.
    #:
    #: `dateOfBirth` — Seccl sends TWO shapes. 285 of 288 staging clients carry
    #: `"1986-04-23T00:00:00.000Z"` (a timezone-aware datetime, which is what the generator saw
    #: in every saved example and typed it as) and 3 carry a bare `"1972-04-25"`. One percent, and
    #: invisible to the vendored collection.
    #:
    #: Harmless downstream today because the conformed model already reads it with
    #: `try_cast(c.date_of_birth AS date)` — checked, not assumed — so the bare form parses and a
    #: shape neither handles would become NULL rather than failing the model. It is the generated
    #: MODEL that is wrong about the vendor, not the pipeline.
    KNOWN_TYPE_DIVERGENCES = frozenset({"dateOfBirth (timezone_aware)"})

    #: The same two sets for ACCOUNTS, kept SEPARATE from the client ones on purpose.
    #:
    #: A divergence excused on an account must not silently excuse the same path on a client: they
    #: are different populations with different generators' inferences behind them, and a shared
    #: allowance would let one endpoint's known gap hide another's new one.
    #:
    #: These were invisible until this check stopped scoping its read to one client that happens
    #: to hold no accounts — it skipped, so the `Account` model was validated against nothing.
    KNOWN_ACCOUNT_OVER_REQUIRED = frozenset(
        {
            # Present on some positions and not others; typed required because every saved
            # example that had an `assetTaxDetails` block had it.
            "positions.assetTaxDetails.taxCalculation",
            # Only a pension that has been flexibly accessed carries a date. 29 accounts.
            "wrapperDetail.productDetail.flexiAccessDate",
        }
    )

    #: Named one at a time WITH THE SHAPE THE VENDOR ACTUALLY SENDS, as the client set is.
    #:
    #: `clients` — **the account's owner list is a list of ID STRINGS** (`'02659H7'`) on this
    #: projection, where the generated model expects `Client` objects because the saved examples
    #: embedded them. 398 occurrences over 380 accounts, so it is the normal shape and not an
    #: edge. Anything reading `account.clients[0].firstName` off a live list read gets an
    #: AttributeError, and `.model_validate` gets this.
    #:
    #: `cgtData.{realised,unrealised}ProfitLoss` — sent as explicit `null`, on the account and on
    #: each position. The generator saw a number in every example and typed it required; a CGT
    #: block with nothing realised legitimately has neither figure.
    KNOWN_ACCOUNT_TYPE_DIVERGENCES = frozenset(
        {
            "clients (model_type)",
            "cgtData.realisedProfitLoss (float_type)",
            "cgtData.unrealisedProfitLoss (float_type)",
            "positions.cgtData.realisedProfitLoss (float_type)",
            "positions.cgtData.unrealisedProfitLoss (float_type)",
        }
    )

    @classmethod
    def _unexpected_missing(
        cls,
        model: Any,
        rows: list[dict[str, Any]],
        *,
        over_required: frozenset[str] | None = None,
        type_divergences: frozenset[str] | None = None,
    ) -> dict[str, int]:
        """Required-but-absent field PATHS the known set does not already account for.

        Paths rather than row indices, counted: one missing field is almost always the same field
        missing on many rows, and reporting the first failing row would make a systematic
        difference read as a one-off. List indices are dropped from the path so
        `updateHistory[3].updateAction` and `updateHistory[7].updateAction` are one finding.
        """
        import collections

        from pydantic import ValidationError

        allowed_missing = cls.KNOWN_OVER_REQUIRED if over_required is None else over_required
        allowed_types = cls.KNOWN_TYPE_DIVERGENCES if type_divergences is None else type_divergences
        found: collections.Counter[str] = collections.Counter()
        for row in rows:
            try:
                model.model_validate(row)
            except ValidationError as exc:
                for error in exc.errors():
                    if error["type"] != "missing":
                        # A TYPE mismatch is a different and worse finding than an absent
                        # optional, so it is excused only by being NAMED in
                        # `KNOWN_TYPE_DIVERGENCES` — never by the missing-field set.
                        label = (
                            f"{'.'.join(str(p) for p in error['loc'] if not isinstance(p, int))}"
                            f" ({error['type']})"
                        )
                        if label not in allowed_types:
                            found[label] += 1
                        continue
                    path = ".".join(str(p) for p in error["loc"] if not isinstance(p, int))
                    if path not in allowed_missing:
                        found[path] += 1
        return dict(found)

    async def test_live_clients_validate_against_the_generated_model(
        self, api: SecclClient
    ) -> None:
        from seccl import models as generated

        rows = await api.clients()
        if not rows:
            pytest.skip("this staging firm has no clients")
        # EVERY row, not a sample: the divergences are per-record and a 25-row window found six
        # of the eight known ones. A few hundred records is a cheap read.
        unexpected = self._unexpected_missing(generated.Client, rows)
        assert not unexpected, (
            "live client records diverge from the model inferred from the Postman collection in "
            f"ways KNOWN_OVER_REQUIRED does not account for: {unexpected}"
        )

    async def test_live_accounts_validate_against_the_generated_model(
        self, api: SecclClient
    ) -> None:
        """The `Account` half of the inference check, against **every account at the firm.**

        It used to scope the read to `clients[0]`, and that client holds none — so it skipped, and
        the generated `Account` model was validated against nothing while appearing to be covered.
        A skip is a quiet way to assert nothing at all, which is the failure mode this whole suite
        is written against.

        `GET /account/list/{firm}` is the same shape as the client check's `GET /client/{firm}`:
        the firm's whole population (380 here against 288 clients), which is what makes the
        divergences it finds worth recording rather than an accident of which record was picked.
        """
        from seccl import models as generated

        rows = await api.accounts()
        if not rows:
            pytest.skip("this staging firm has no accounts")
        unexpected = self._unexpected_missing(
            generated.Account,
            rows,
            over_required=self.KNOWN_ACCOUNT_OVER_REQUIRED,
            type_divergences=self.KNOWN_ACCOUNT_TYPE_DIVERGENCES,
        )
        assert not unexpected, f"live account records diverge unexpectedly: {unexpected}"

    async def test_the_known_divergences_are_still_real(self, api: SecclClient) -> None:
        """The other half of `KNOWN_OVER_REQUIRED`: a path that stops diverging should leave the
        set, or it becomes a list of things that used to be true.

        Failing here is good news — it means Seccl started sending the field, or the generator's
        inference was fixed.
        """
        import collections

        from pydantic import ValidationError

        from seccl import models as generated

        rows = await api.clients()
        if not rows:
            pytest.skip("this staging firm has no clients")
        seen: collections.Counter[str] = collections.Counter()
        for row in rows:
            try:
                generated.Client.model_validate(row)
            except ValidationError as exc:
                for error in exc.errors():
                    if error["type"] == "missing":
                        seen[".".join(str(p) for p in error["loc"] if not isinstance(p, int))] += 1
        stale = sorted(self.KNOWN_OVER_REQUIRED - set(seen))
        assert not stale, (
            f"{stale} no longer diverge on live data — remove them from KNOWN_OVER_REQUIRED so "
            "the set keeps describing what is actually true."
        )

    def test_a_model_has_no_read_shape_to_check(self) -> None:
        """`seccl.models` exposes `ModelCreate` and no `Model`, which is not an omission: the
        vendored collection carries no saved RESPONSE for a model read, so there was nothing to
        infer one from. Stated as a test so the gap is visible — a future collection refresh that
        adds one should add the validation above rather than leaving this passing."""
        from seccl import models as generated

        assert hasattr(generated, "ModelCreate")
        assert not hasattr(generated, "Model"), (
            "a Model read shape now exists — validate live model rows against it, as the client "
            "and account reads are validated above"
        )

    async def test_product_type_is_a_list_despite_the_singular_name(self, api: SecclClient) -> None:
        """A fact the conformed layer depends on and the name argues against: a model can serve
        GIA, ISA, JISA and PENSION at once. `platform.models.products` is a `product_type[]`
        because of it, and reading it as a scalar is how the models listing rendered nothing."""
        rows = await api.models()
        typed = [row for row in rows if row.get("productType") is not None]
        if not typed:
            pytest.skip("no model on this firm states a productType")
        for row in typed[:10]:
            assert isinstance(row["productType"], list), (
                f"productType came back as {type(row['productType']).__name__} — the conformed "
                "layer and `matchesAny` both read it as a list"
            )


class TestTheFeeRegisterQuirks:
    """Three things `services/seccl` had to learn the hard way, all live-checkable."""

    async def test_an_empty_register_is_a_real_answer(self, api: SecclClient) -> None:
        """Verified live once (04 Aug 2026: `meta.count` 0) and the reason `raw.fee_structures`
        declares its whole read shape as a spine — with no rows to infer from, `tiers` and `type`
        would not exist as columns and the conformed model would fail to bind on precisely the
        firm that has no fee structures.

        So an empty answer PASSES, and the assertion is that it comes back as an empty list
        rather than an error or a null.
        """
        rows = await api.fee_structures()
        assert isinstance(rows, list)

    async def test_a_rate_is_a_fraction_not_a_percentage(self, api: SecclClient) -> None:
        """`0.0035` means 0.35%. The conformed layer scales it, and getting this backwards
        misprices every invoice by a factor of 100 — the `_allocation_lines` trap again."""
        rows = await api.fee_structures()
        rates = [
            tier["rate"]
            for row in rows
            for tier in (row.get("tiers") or [])
            if isinstance(tier, dict) and isinstance(tier.get("rate"), (int, float))
        ]
        if not rates:
            pytest.skip("this staging firm publishes no fee tiers")
        assert all(0 <= rate < 1 for rate in rates), (
            f"a fee rate outside [0,1) means Seccl now publishes percentages, not fractions: "
            f"{[r for r in rates if not (0 <= r < 1)][:5]}"
        )

    async def test_tier_bands_start_at_the_next_penny(self, api: SecclClient) -> None:
        """Seccl starts the next band at `10000.01`, which `catalogue.validate_fee_rule` rejects
        as a gap — so the conformed layer normalises them contiguous. If Seccl ever changed to
        contiguous bands the normalisation would start shifting real boundaries."""
        rows = await api.fee_structures()
        for row in rows:
            tiers = [t for t in (row.get("tiers") or []) if isinstance(t, dict)]
            bounds = [(t.get("from"), t.get("to")) for t in tiers]
            pairs = [
                (previous_to, next_from)
                for (_, previous_to), (next_from, _) in zip(bounds, bounds[1:], strict=False)
                if isinstance(previous_to, (int, float)) and isinstance(next_from, (int, float))
            ]
            if not pairs:
                continue
            assert any(
                abs(next_from - previous_to - 0.01) < 1e-9 for previous_to, next_from in pairs
            ), (
                f"no next-penny band boundary in {row.get('id')} — the conformed layer's "
                "normalisation assumes Seccl leaves a penny gap; contiguous bands would make it "
                "shift real boundaries"
            )


class TestThePathsAreStillWhereWeThinkTheyAre:
    """`test_endpoint_paths.py` pins every path against the vendored collection. This asks the
    API, which is what moves."""

    async def test_the_one_versioned_path_is_still_versioned(self, api: SecclClient) -> None:
        """`GET /v1/fee-structures/{firm}` is the ONLY `/v1/` segment in the read set — an
        inconsistency on Seccl's side that a caller has to know about, and one that would break
        silently if they ever normalised it away.

        Asserted by CALLING it: the path is built as `_paths.scoped("v1/fee-structures", …)`, so
        a 404 here is the version prefix having moved, which is the thing worth learning.
        """
        assert isinstance(await api.fee_structures(), list)


class TestTheWritePaths:
    async def test_a_client_can_be_created_and_read_back(
        self, api: SecclClient, writes_allowed: None
    ) -> None:
        """The write models are inferred too, and their `required` is the softest inference in
        this package — "present in every example" is not the API's own requiredness, which is why
        `platform_contracts` does NOT enforce Seccl requiredness and shows the marker as guidance.

        **A live create is the only way to learn the real answer, and it disagrees in BOTH
        directions.** Asked with a nearly-empty body, Seccl names twelve required fields in
        `fieldErrors`; the generated `ClientCreate` marks ten of them optional. So the inference
        is not only over-required in places (`KNOWN_OVER_REQUIRED`, found on the first live run)
        but under-required in others — and the two fail differently: an over-required field fails
        a read of a good record, an under-required one lets a create go out that Seccl refuses.

        It creates a client every run, which is fine here and is a deliberate choice: Seccl has
        delete, a client is cheap, and a create that only ever ran once against an empty firm
        would stop testing anything the moment the firm was not empty. It briefly reused a
        long-lived anchor and skipped instead, which is a quiet way of asserting nothing.

        The payload it used before was wrong in a way only a live call shows: `lastName` (a field
        Seccl does not have, silently ignored) and no node. `anchors.client_payload` carries the
        vendor's own required set now.
        """
        from . import anchors

        node = await self._sandbox_node(api)
        # Its OWN identity within the run: the lifecycle suite creates a client too, and Seccl
        # registers an email to exactly one client.
        created = await api.create_client(anchors.client_payload(node, suffix="C"))
        assert anchors.record_id(created), (
            f"created a client and could not read its id out of {sorted(created)}"
        )

    async def test_the_vendor_states_its_own_required_fields(
        self, api: SecclClient, writes_allowed: None
    ) -> None:
        """**The finding that makes the inference checkable**, asserted rather than remembered.

        A `POST /client` that omits everything answers `400 DataValidationError` with a
        `fieldErrors` array naming each missing field — which is the vendor's own requiredness,
        the thing `build_schemas.py` can only guess at from examples. Recorded here so that the
        day Seccl relaxes or tightens it, this says so.

        The probe deliberately sends a body that CANNOT succeed, so nothing is created.
        """
        from seccl._base import SecclApiError

        with pytest.raises(SecclApiError) as raised:
            await api.create_client({"clientType": "Individual", "nodeId": ["0"]})
        # Whether it is the node (404) or the fields (400) that refuses first is not the point;
        # what matters is that a refusal is specific enough to act on.
        assert "40" in str(raised.value)

    async def _sandbox_node(self, api: SecclClient) -> str:
        """A node it is safe to create test records under — see the lifecycle suite's fixture,
        which carries the reasoning. Duplicated as a helper rather than shared, because a
        conftest fixture would make this file depend on the lifecycle's ordering."""
        from . import anchors

        for node in await api.firm_nodes():
            name = str(node.get("name") or "").lower()
            found = anchors.record_id(node) or str(node.get("nodeId") or "")
            if found and any(w in name for w in ("sandbox", "demo", "without advisers")):
                return found
        pytest.skip("no sandbox-looking node on this firm; set SECCL_INTEGRATION_NODE_ID")
