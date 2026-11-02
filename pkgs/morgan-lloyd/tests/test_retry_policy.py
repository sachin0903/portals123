"""Every retry loop in this client goes through the shared policy, stating its METHOD.

The behaviour is tested once, in `pkgs/api-log/tests/test_retry.py` — one predicate, one place.
What has to be asserted per client is the WIRING, because the regression that produced the shared
policy was a CALL SITE moving onto an existing loop rather than the predicate being wrong.

Here, Dynamics HAS NO DELETE, so a retried `cam_createcontact` puts a second human being in the
CRM and a retried contribution reclaims relief from HMRC twice.

So the rule is that `_base.py` may not configure a retry itself. `AsyncRetrying(**retry_policy(m))`
or nothing — no local `retry=`, no local `stop=`, no second attempt count to diverge on. Asserted
on the source, the pattern `apps/api` uses for a rule a test cannot drive end to end
(`test_email_send_guard`, `test_actions_tripwire`).
"""

from __future__ import annotations

import re
from pathlib import Path

_SOURCE = (Path(__file__).resolve().parent.parent / "src" / "morgan_lloyd" / "_base.py").read_text()


def test_every_retry_loop_uses_the_shared_policy() -> None:
    loops = re.findall(r"AsyncRetrying\(([^)]*\)?[^)]*)\)", _SOURCE)
    assert loops, "expected to find the retry loops"
    for loop in loops:
        assert loop.startswith("**retry_policy("), f"hand-configured retry loop: {loop}"


def test_no_retry_is_configured_locally() -> None:
    """A `stop_after_attempt` or a `retry_if_exception` here is a second answer to a question the
    shared module owns — which is how the counts came to be 5, 5 and 4 with nothing saying why."""
    for banned in ("stop_after_attempt", "retry_if_exception", "wait_exponential"):
        assert banned not in _SOURCE, f"{banned} is the shared policy's to decide"


def test_the_method_reaches_the_policy() -> None:
    """A literal would be the method-blind predicate again in another spelling. Every loop names
    either the request's own `method` or a hardcoded idempotent one, which is legitimate for a
    loop that only ever reads."""
    for loop in re.findall(r"\*\*retry_policy\(([^)]*)\)", _SOURCE):
        assert loop == "method" or loop.strip("\"'").upper() in {"GET", "HEAD", "OPTIONS"}, (
            f"retry_policy({loop}) is neither the request's method nor an idempotent literal"
        )
