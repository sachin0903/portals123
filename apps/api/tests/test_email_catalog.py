"""Tripwire: the email-code catalogue must match the codes the send path actually resolves.

The authoring UI offers ``email_catalog.KNOWN_CODES`` as the selectable email types. If a code is
listed but no send site uses it, a firm would author a template that never sends; if a send site
uses a code that isn't listed, that trigger is invisible to authors. Both are drift — this test
fails on either, by scanning the send sites for ``render_or_default(code="...")`` literals.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.email import catalog as email_catalog
from app.email.catalog import KNOWN_CODES

# The live send sites — the files that call render_or_default. Keep in step with email_catalog:
# `test_every_listed_send_site_actually_sends` fails when a listed file no longer sends.
#
# `app/tasks/notifier.py` was here until the task emails became rule-driven: the scan now emits a
# `task.assigned` / `task.due_soon` action event and the notification email subscriber renders
# `task_assigned` / `task_reminder` from the rule's `email_code` — so those two codes are credited
# by the DYNAMIC branch below, like every other notification template.
_SEND_SITES = [
    "app/provisioning.py",
    "app/routers/internal.py",
    "app/notifications/email_consumer.py",
]

#: The whole `code=` EXPRESSION, not just its first string: a send site may choose between two
#: literals, and reading only the first would report the other as a code with no send site.
_CODE_RE = re.compile(r'code\s*=\s*("[a-z_]+"(?:\s+if\s+[^\n]*?else\s+"[a-z_]+")?)')
_LITERAL_RE = re.compile(r'"([a-z_]+)"')


def _used_codes() -> set[str]:
    """Every ``code=`` literal passed to a ``render_or_default(`` call in the send sites. Splitting
    on the call and taking the first ``code=`` in each segment is robust to multi-line calls and to
    any parentheses in the preceding args (which a single regex with ``[^)]`` would trip on)."""
    root = Path(__file__).resolve().parents[1]  # apps/api
    used: set[str] = set()
    for rel in _SEND_SITES:
        text = (root / rel).read_text(encoding="utf-8")
        for segment in text.split("render_or_default(")[1:]:
            match = _CODE_RE.search(segment[:600])
            if match:
                used |= set(_LITERAL_RE.findall(match.group(1)))
    # The consumer resolves a DYNAMIC per-rule code, so credit every selectable notification one.
    consumer = (root / "app/notifications/email_consumer.py").read_text(encoding="utf-8")
    if "rule.email_code" in consumer and "render_or_default(" in consumer:
        used |= email_catalog.notification_email_codes()
    return used


def test_every_catalogue_code_is_actually_sent() -> None:
    used = _used_codes()
    listed = {c.code for c in KNOWN_CODES}
    unsent = listed - used
    assert not unsent, f"catalogue codes with no send site (would never send): {sorted(unsent)}"


def test_every_sent_code_is_in_the_catalogue() -> None:
    used = _used_codes()
    listed = {c.code for c in KNOWN_CODES}
    missing = used - listed
    assert not missing, f"send-site codes missing from the catalogue: {sorted(missing)}"


def test_every_listed_send_site_actually_sends() -> None:
    """A stale entry is not harmless: this module discovers codes by reading THESE files, so a file
    that no longer resolves a template contributes nothing and both checks above get quietly
    weaker."""
    root = Path(__file__).resolve().parents[1]
    for rel in _SEND_SITES:
        text = (root / rel).read_text(encoding="utf-8")
        assert "render_or_default(" in text, (
            f"{rel} is listed as an email send site but no longer calls render_or_default — "
            "remove it, or point the list at wherever that code moved to."
        )


def test_catalogue_has_no_duplicate_codes() -> None:
    codes = [c.code for c in KNOWN_CODES]
    assert len(codes) == len(set(codes))
