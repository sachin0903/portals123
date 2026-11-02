"""Unit tests for filename-based document auto-classification (app.routers.documents).

Pure logic — no DB. Exercises `_pick_document_type` against the seeded default vocabulary and a
custom tenant type: label/slug matches, the default synonym hints, and the 'other' fallback.
"""

from app.routers.documents import _pick_document_type

# The seeded default tags (documents.seed_default_document_types).
DEFAULTS = [
    {"slug": "report", "label": "Report"},
    {"slug": "statement", "label": "Statement"},
    {"slug": "agreement", "label": "Agreement"},
    {"slug": "identity", "label": "Identity"},
    {"slug": "tax", "label": "Tax"},
    {"slug": "illustration", "label": "Illustration"},
    {"slug": "application", "label": "Application"},
    {"slug": "letter", "label": "Letter"},
    {"slug": "fatca_form", "label": "W-9 / W-8"},
]


def test_matches_on_label_token() -> None:
    assert _pick_document_type("Jan Statement 2026.pdf", DEFAULTS) == "statement"
    assert _pick_document_type("Investment Report Q1.pdf", DEFAULTS) == "report"


def test_matches_via_default_synonym_hints() -> None:
    assert _pick_document_type("client_passport.jpg", DEFAULTS) == "identity"
    assert _pick_document_type("SIPP Illustration.pdf", DEFAULTS) == "illustration"
    assert _pick_document_type("HMRC_tax_return.pdf", DEFAULTS) == "tax"
    assert _pick_document_type("Portfolio valuation.pdf", DEFAULTS) == "statement"
    assert _pick_document_type("ISA application form.pdf", DEFAULTS) == "application"


def test_unrecognised_name_returns_no_match() -> None:
    # No confident match ⇒ blank (never 'other'/a default): the adviser classifies manually.
    assert _pick_document_type("scan0012.pdf", DEFAULTS) == ""


def test_custom_tenant_type_matches_on_its_own_tokens() -> None:
    types = [{"slug": "kyc_pack", "label": "KYC Pack"}]
    assert _pick_document_type("Onboarding KYC pack.pdf", types) == "kyc_pack"


def test_no_types_returns_no_match() -> None:
    assert _pick_document_type("anything.pdf", []) == ""


def test_unmatched_name_does_not_fall_back_to_first_type() -> None:
    types = [{"slug": "report", "label": "Report"}, {"slug": "tax", "label": "Tax"}]
    assert _pick_document_type("mystery.pdf", types) == ""


def test_a_w9_or_w8_is_recognised_however_it_is_spelt() -> None:
    for name in ("W-9.pdf", "w9 signed.pdf", "fw9.pdf", "W-8BEN.pdf", "W8-BEN-E scan.png"):
        assert _pick_document_type(name, DEFAULTS) == "fatca_form", name
    assert _pick_document_type("FATCA self-certification.pdf", DEFAULTS) == "fatca_form"


def test_the_word_form_alone_does_not_pick_the_w9_w8_type() -> None:
    """`fatca_form` carries "form" in its slug, and most forms are not a W-9."""
    assert _pick_document_type("Transfer form.pdf", DEFAULTS) == ""
    assert _pick_document_type("ISA application form.pdf", DEFAULTS) == "application"
