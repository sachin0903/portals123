"""What an email is ABOUT — the claim the editor's context panel edits.

Asked at creation and revisable afterwards, because it decides which event details the panel lists.
Without it the list shows every field of every subject, which is the "way too many" state.
"""

from __future__ import annotations

import pytest


class TestDeclaredSubjects:
    def test_normalises_to_a_canonical_order_and_collapses_duplicates(self) -> None:
        from app.email.templates_service import normalise_subjects as n

        # Order is imposed, not preserved: two templates claiming the same set store the same bytes.
        assert n(["account", "contact", "contact"]) == ["contact", "account"]
        assert n(["document", "task"]) == ["task", "document"]

    def test_absent_and_empty_are_ONE_state(self) -> None:
        from app.email.templates_service import normalise_subjects as n

        assert n(None) == []
        assert n([]) == []
        assert n(["  "]) == []

    def test_refuses_a_kind_no_trigger_can_carry(self) -> None:
        from app.email.templates_service import InvalidSubjects
        from app.email.templates_service import normalise_subjects as n

        # The author picked from a fixed list, so anything else is a client bug, not a typo.
        with pytest.raises(InvalidSubjects, match="not something an email can be about"):
            n(["portfolio"])
        with pytest.raises(InvalidSubjects, match="must be a list"):
            n("account")

    def test_the_kinds_are_exactly_what_the_notification_context_can_resolve(self) -> None:
        # Offering a subject the consumer cannot resolve puts a detail in the panel no send can
        # fill — the shape of the gap this claim closes.
        from app.email.templates_service import EMAIL_SUBJECT_KINDS
        from app.notifications.context import SCOPE_FIELDS

        assert set(EMAIL_SUBJECT_KINDS) == {k for k in SCOPE_FIELDS if k is not None}

    def test_the_schema_drops_an_empty_claim_and_keeps_a_real_one(self) -> None:
        from app.email.templates_service import _with_normalised_subjects as w

        assert w({"subjects": ["account"]})["subjects"] == ["account"]
        assert "subjects" not in w({"subjects": []})
        # A schema that never mentions them is returned untouched — every other writer of this jsonb
        # (restore, clone, the model preset) must be unaffected.
        plain = {"html": "<p>x</p>"}
        assert w(plain) is plain
