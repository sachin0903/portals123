"""One country list, stored as a code, displayed as a name.

`ref.countries` (0050) is the estate's country reference — 250 rows of the current ISO 3166-1
alpha-2 set, with `adjective` for nationality labels and `identifiers` for what an onboarding form
validates a passport against. It was already the source for `nationality` and an individual's
`tax_residency`, which is why those store codes.

TWO FIELDS WERE NOT READING IT, and both stored a display NAME instead:

* `country_of_birth` — a hardcoded 40-odd-name list in the seed, so `crm.contacts` held
  "United Kingdom" beside a `nationality` of `{GB}`. One fact, two spellings, in one row:
  unjoinable to `ref.countries` and unusable at any boundary that wants ISO (ML, CTC, ComplyCube
  all take codes).
* an ORGANISATION's `taxResidency` — the same question an individual answers with a code.

Both now read `{"source": "countries", "valueKey": "code", "labelKey": "name"}`, and the record's
own query resolves the names back so nothing shows "GB" as a fact about where someone was born.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# No `pytestmark`: `asyncio_mode = "auto"` (pyproject) already runs the coroutines, and marking a
# SYNC test with it is a warning per test.
_SEED = Path(__file__).resolve().parent.parent / "migrations" / "sql" / "0050_ref_data.sql"


def _seeded_codes() -> set[str]:
    body = _SEED.read_text(encoding="utf-8").split("INSERT INTO ref.countries", 1)[1]
    return set(re.findall(r"\(\s*'([A-Z]{2})'\s*,", body))


class TestTheReferenceListItself:
    async def test_it_is_the_current_iso_set(self, service_session: AsyncSession) -> None:
        rows = (
            await service_session.execute(text("SELECT count(*) FROM ref.countries"))
        ).scalar_one()
        assert rows == 250

    def test_it_carries_the_post_2010_iso_changes(self) -> None:
        """The seven codes that separate a CURRENT list from a pre-2010 one, and the reason the
        seed's own hardcoded list was not merely shorter but stale:

          BQ / CW / SX  the Netherlands Antilles successors (Oct 2010)
          SS            South Sudan (2011)
          BL / MF       Saint Barthélemy / Saint Martin, split from Guadeloupe (2007)
          AX            Åland Islands

        A list without them also tends to carry `AN` (Netherlands Antilles, withdrawn) — so the
        presence of these is what makes this the list to populate a picker from.
        """
        codes = _seeded_codes()
        assert {"BQ", "CW", "SX", "SS", "BL", "MF", "AX"} <= codes

    def test_it_does_not_carry_withdrawn_or_non_iso_codes(self) -> None:
        """`AN` was withdrawn when the Netherlands Antilles dissolved; `GZ` (Gaza Strip) is a
        geo-targeting code that ISO 3166-1 never assigned. Either one in a stored value is a code
        that resolves to nothing for the rest of its life."""
        codes = _seeded_codes()
        assert "AN" not in codes
        assert "GZ" not in codes

    async def test_every_code_is_two_upper_case_letters(
        self, service_session: AsyncSession
    ) -> None:
        """What the columns store, so a stray lower-case or three-letter row would be a value no
        form could post back and no vendor would accept."""
        bad = (
            (
                await service_session.execute(
                    text("SELECT code FROM ref.countries WHERE code !~ '^[A-Z]{2}$'")
                )
            )
            .scalars()
            .all()
        )
        assert not bad

    async def test_every_country_has_a_display_name(self, service_session: AsyncSession) -> None:
        missing = (
            (
                await service_session.execute(
                    text("SELECT code FROM ref.countries WHERE name IS NULL OR btrim(name) = ''")
                )
            )
            .scalars()
            .all()
        )
        assert not missing, f"no name to display for {missing}"


class TestTheSeedReadsIt:
    """The seed is where a country question is defined, so this is where the switch is pinned."""

    def test_no_country_question_carries_a_hardcoded_list(self) -> None:
        """The specific regression: `_opts(COUNTRIES)` on a country field. The list itself is
        deleted, so this asserts nothing reintroduces one — a new hardcoded list would be a
        second country vocabulary the moment `ref.countries` changed."""
        from app.flows import onboarding_seed

        source = Path(onboarding_seed.__file__).read_text(encoding="utf-8")
        assert "COUNTRIES = [" not in source
        assert not hasattr(onboarding_seed, "COUNTRIES")

    def test_country_fields_read_the_reference_source(self) -> None:
        from app.flows import onboarding_seed

        wanted = {"country_of_birth", "tax_residency", "taxResidency"}
        found: dict[str, object] = {}
        for name in dir(onboarding_seed):
            value = getattr(onboarding_seed, name, None)
            if not isinstance(value, list):
                continue
            for row in value:
                if isinstance(row, tuple) and len(row) >= 8 and row[0] in wanted:
                    found[str(row[0])] = row[7]
        assert set(found) == wanted, f"missing {sorted(wanted - set(found))}"
        for field, options in found.items():
            assert options == {
                "source": "countries",
                "valueKey": "code",
                "labelKey": "name",
            }, f"{field} does not read ref.countries by code"

    def test_the_nationality_fields_keep_their_own_source(self) -> None:
        """`nationalities`, not `countries`, and the difference is not cosmetic: `resolveOptions`
        special-cases that source to prefer `adjective`, so a nationality picker offers "British"
        where a country-of-birth picker must offer "United Kingdom". You are born in a country,
        not in a nationality — and both resolve against the same `ref.countries` rows.

        Read STRUCTURALLY, not by counting the string in the file: the comments in this change
        quote `{"source": "nationalities"}` while explaining it, which is the false positive
        `switch-columns.test.ts` already records hitting.
        """
        from app.flows import onboarding_seed

        found = {
            str(row[0]): row[7]
            for name in dir(onboarding_seed)
            if isinstance(getattr(onboarding_seed, name, None), list)
            for row in getattr(onboarding_seed, name)
            if isinstance(row, tuple) and len(row) >= 8 and row[0] == "nationality"
        }
        assert set(found) == {"nationality"}
        for field, options in found.items():
            assert options == {"source": "nationalities"}, f"{field} changed source"


class TestTheRecordResolvesTheNameBack:
    async def test_a_code_reads_back_as_a_country(self, service_session: AsyncSession) -> None:
        """The read's own join. Done in the API rather than the portals because it is one LEFT
        JOIN against a table every principal may read — a client-side lookup would cost each
        surface a fetch and a fallback copy of the list."""
        suffix = uuid.uuid4().hex[:8]
        tenant = int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) "
                        "RETURNING id"
                    ),
                    {"u": f"ctr_{suffix}", "a": f"ctr-{suffix}", "n": f"Countries {suffix}"},
                )
            ).scalar_one()
        )
        contact = int(
            (
                await service_session.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, name, first_name, "
                        "  surname, email, country_of_birth, nationality, tax_residency) "
                        "VALUES (:t, :u, 'individual', 'Ola Berg', 'Ola', 'Berg', :e, 'SE', "
                        "  ARRAY['SE','GB'], ARRAY['GB']) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "u": f"c{suffix}0000"[:12],
                        "e": f"ola.{suffix}@example.com",
                    },
                )
            ).scalar_one()
        )
        row = (
            (
                await service_session.execute(
                    text(
                        "SELECT c.country_of_birth, "
                        "  (SELECT rc.name FROM ref.countries rc "
                        "    WHERE rc.code = c.country_of_birth) AS country_of_birth_name, "
                        "  (SELECT array_agg(rc.name ORDER BY x.ord) "
                        "     FROM unnest(c.nationality) WITH ORDINALITY AS x(code, ord) "
                        "     JOIN ref.countries rc ON rc.code = x.code) AS nationality_names "
                        "FROM crm.contacts c WHERE c.id = :c"
                    ),
                    {"c": contact},
                )
            )
            .mappings()
            .one()
        )
        assert row["country_of_birth"] == "SE"
        assert row["country_of_birth_name"] == "Sweden"
        # ORDER PRESERVED. `array_agg` over a plain unnest has no defined order, so the
        # ordinality is what stops a dual national's nationalities reshuffling between reads —
        # the same rule the vulnerability drivers follow.
        assert row["nationality_names"] == ["Sweden", "United Kingdom"]

    def test_the_read_resolves_names_from_the_cached_catalogue(self) -> None:
        """The record still returns the three name fields. They come from the process cache
        of ``ref.countries``, not a join on every client read."""
        source = (Path(__file__).resolve().parent.parent / "app" / "routers" / "crm.py").read_text(
            encoding="utf-8"
        )
        assert "await country_catalogue()" in source
        for field in (
            'record["country_of_birth_name"]',
            'record["nationality_names"]',
            'record["tax_residency_names"]',
        ):
            assert field in source, f"the client read does not resolve {field}"
        assert "JOIN ref.countries" not in source

    async def test_an_unknown_code_resolves_to_null_rather_than_failing(
        self, service_session: AsyncSession
    ) -> None:
        """A withdrawn code on an old record (`AN`) must not break the read — the UI falls back to
        showing the code, which is better than an empty row claiming no country of birth."""
        resolved = (
            await service_session.execute(text("SELECT name FROM ref.countries WHERE code = 'AN'"))
        ).scalar_one_or_none()
        assert resolved is None

    async def test_greece_identifier_labels_are_english(
        self, service_session: AsyncSession
    ) -> None:
        """UAT-8: localeName used to be Greek and the tax-id field title followed it."""
        labels = (
            (
                await service_session.execute(
                    text(
                        "SELECT ident->>'localeName' FROM ref.countries, "
                        "       jsonb_array_elements(identifiers) AS ident "
                        "WHERE code = 'GR'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert labels
        for label in labels:
            assert label is not None
            assert all(ord(ch) < 128 for ch in label)
