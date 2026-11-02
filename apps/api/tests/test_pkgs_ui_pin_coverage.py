"""Every test that reads PORTAL source must carry @pytest.mark.pkgs_ui_pin, or the
api:test-contracts moon task (docs: apps/api/moon.yml) silently stops covering it.

"Portal source" is pkgs/ui and a portal app's own TypeScript. The marker is named for the first
because that was the only case when it was written; the reason is the same for both and is the
one its own description gives — the API container ships without either, so a test reading the
real file is the only thing keeping a rule written in two languages honest.

This exists because grep-based conventions turned out not to be trustworthy: a real audit found
cross-language pins self-documented three different, inconsistent ways (a `_CONTRACT_PATH` +
`test_contract_*` naming pair, a "THE CROSS-LANGUAGE PIN" docstring with no naming convention, and
plain prose in a module docstring with no marker at all) — and separately found `-k contract`
matching 17 tests that have nothing to do with pkgs/ui at all (vendor API "contracts" in
test_platform_actions.py, an HTTP "wire contract" in test_documents_api.py). Neither name-substring
matching nor docstring conventions can be trusted; only an explicit marker can.

The signal used here — "mentions pkgs/ui AND actually reads a file" — is deliberately narrower
than "mentions pkgs/ui": a test that only documents a hardcoded correspondence in a comment,
without reading the real source to verify it, is a different (and weaker) kind of test — it
cannot catch drift either way, so re-running it under test-contracts would buy nothing. Known
examples of that weaker shape (accepted as-is, not violations): test_crm_scaling.py,
test_crm_listing_filters.py::TestClientStatus, test_listing.py.
"""

import re
from pathlib import Path

TESTS_DIR = Path(__file__).parent


def test_every_pkgs_ui_reading_test_file_carries_the_pin_marker() -> None:
    unmarked = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name == "test_pkgs_ui_pin_coverage.py":
            continue
        text = path.read_text(encoding="utf-8")
        mentions_portal = "pkgs/ui" in text or "apps/client" in text
        if mentions_portal and re.search(r"\.read_text\(", text) and "pkgs_ui_pin" not in text:
            unmarked.append(path.name)
    assert not unmarked, (
        f"{unmarked} reference portal source AND read a file from disk, but carry no "
        "@pytest.mark.pkgs_ui_pin anywhere — api:test-contracts will silently skip them. Mark the "
        "specific test/class (or the whole module via `pytestmark = pytest.mark.pkgs_ui_pin` if "
        "every test in the file is the pin), or if it genuinely doesn't verify against pkgs/ui "
        "live, add it to the accepted-exception list in this test's own docstring."
    )
