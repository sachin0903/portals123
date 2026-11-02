"""``POST /internal/forgot-password`` — whose brand the reset email wears, and why choosing it
cannot leak anything.

The form is unauthenticated, so the endpoint's defining property is that it answers identically
whatever the truth is: same status, same body, whether the address exists, whether it belongs to
staff or to a client, whether the firm is suspended, whether the throttle bit. ``first_name`` is
hard-coded to ``None`` for the same reason — a personalised reset mail is itself an
account-existence oracle.

**None of that constrains the BRAND**, because only the account owner ever receives the email. What
DID constrain it was an omission: the send resolved ``brand_for(session, tenant_id)`` with no
``portal``, taking the ``client`` default for every caller — so a member of a firm's staff resetting
their password from the platform portal received the client-portal branding and the client-portal
sender identity, in the one white-label seam where "which of the firm's two faces is this" is the
whole question. Every other send site resolves a portal (``tasks/notifier.py`` from the assignee
kind, ``provisioning.py`` from the portal being granted); this one alone guessed.

The host it was posted from is the answer, and it was already in hand: a tenant environment
registers its client-portal and platform hosts separately, and ``env_config_by_host`` has always
derived which one a host is (it is how the config's own ``branding`` is chosen) — it just did not
return it.

So two things are pinned here: the resolution itself (end to end, through the real config read, the
real brand resolution, the real render and the real send path), and that the RESPONSE is byte-equal
across both portals and an address that exists on neither.
"""

from __future__ import annotations

import json
import types
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import Engine, text

from app import environments, ratelimit
from app.comms import stream
from app.config import settings
from app.email import transport as email_mod
from app.routers import internal
from tests import email_support

#: The two portals' branding, made distinguishable in the delivered bytes. The firm NAME comes from
#: ``auth.tenants.name`` and is shared, so the accent (which reaches the HTML) and the support
#: address (which forms the envelope) are what separate them.
CLIENT_ACCENT, PLATFORM_ACCENT = "#aa0011", "#0022bb"
CLIENT_SUPPORT, PLATFORM_SUPPORT = "clients@verified.test", "staff@verified.test"

#: ``settings.email_from``'s domain, so ``resolve_sender`` may use a support address as the ``From``
#: — which makes the envelope assertion sharp rather than a Reply-To detail.
VERIFIED_FROM = "Graphene <no-reply@verified.test>"

THEME: dict[str, Any] = {
    "client": {
        "colors": {"light": {"primary": CLIENT_ACCENT}},
        "support": {"email": CLIENT_SUPPORT},
    },
    "platform": {
        "colors": {"light": {"primary": PLATFORM_ACCENT}},
        "support": {"email": PLATFORM_SUPPORT},
    },
}


class _MintingWorkOS:
    """Answers ``reset_password`` with a token. The mint is not what is under test here — it is
    what has to happen before there is an email to brand at all."""

    def __init__(self) -> None:
        self.mints: list[str] = []

    @property
    def user_management(self) -> Any:
        return types.SimpleNamespace(reset_password=self._reset)

    async def _reset(self, *, email: str, **_kw: Any) -> Any:
        self.mints.append(email)
        return types.SimpleNamespace(password_reset_token=f"rt_{uuid.uuid4().hex[:8]}")


@pytest.fixture
def firm(super_engine: Engine) -> dict[str, str]:
    """A firm branded differently per portal, with BOTH portal hosts registered — the shape the
    resolution needs and the shape production has."""
    made = email_support.make_firm(
        super_engine, prefix="fp", name="Redbourne", email_domain="verified.test", theme=THEME
    )
    suffix = uuid.uuid4().hex[:8]
    hosts = {"client": f"portal-{suffix}.test", "platform": f"staff-{suffix}.test"}
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, workos_client_id, firm_org_id, "
                "   client_portal_host, platform_host) "
                "VALUES (:t, 'production', :cid, :org, :ch, :ph)"
            ),
            {
                "t": int(made["tenant_id"]),
                "cid": f"client_{suffix}",
                "org": f"org_firm_{suffix}",
                "ch": hosts["client"],
                "ph": hosts["platform"],
            },
        )
        c.commit()
    return {**made, "client_host": hosts["client"], "platform_host": hosts["platform"]}


@pytest_asyncio.fixture
async def service_sessions(db: dict[str, str]):
    """``service_role`` sessions — what ``internal_session`` hands the endpoint."""
    opener, engine = email_support.session_opener(db, {}, role="service_role")
    yield opener
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clean_email_state(monkeypatch: pytest.MonkeyPatch):
    """Cold brand/sender caches (both are keyed ``(tenant_id, portal)``, so a warm one from the
    other portal would be exactly the bug under test), a known verified sender, and mail permitted
    to leave."""
    email_mod.clear_caches()
    ratelimit.limiter.reset()
    monkeypatch.setattr(settings, "email_from", VERIFIED_FROM)
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(settings, "email_allowlist", ["@verified.test"])
    yield
    email_mod.clear_caches()
    ratelimit.limiter.reset()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(
        email_mod, "httpx", types.SimpleNamespace(AsyncClient=email_support.FakeClient(payloads))
    )
    return payloads


async def _forgot(service_sessions, host: str, email: str) -> dict[str, bool]:
    async with service_sessions() as session:
        return await internal.forgot_password(
            body=internal.ForgotPassword(email=email, host=host), session=session
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("which", "accent", "support", "other_accent"),
    [
        ("platform_host", PLATFORM_ACCENT, PLATFORM_SUPPORT, CLIENT_ACCENT),
        ("client_host", CLIENT_ACCENT, CLIENT_SUPPORT, PLATFORM_ACCENT),
    ],
    ids=["a staffer resetting from the platform portal", "a client resetting from theirs"],
)
async def test_the_reset_email_wears_the_brand_of_the_portal_it_was_asked_from(
    service_sessions,
    firm: dict[str, str],
    sent: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    which: str,
    accent: str,
    support: str,
    other_accent: str,
) -> None:
    """The fix, end to end and in both directions. The platform case is the one that was broken —
    it took ``brand_for``'s ``client`` default — and the client case is the control that stops a
    fix reading "always platform" as a pass.

    Both halves of the identity are asserted, because ``brand_for`` feeds both: the accent reaches
    the delivered HTML and the per-portal support address forms the envelope."""
    workos = _MintingWorkOS()
    monkeypatch.setattr(environments, "core_workos_client", lambda: workos)

    assert await _forgot(service_sessions, firm[which], firm["email"]) == {"ok": True}
    assert workos.mints == [firm["email"]]  # …so there really was an email to brand
    assert len(sent) == 1
    assert accent in sent[0]["html"]
    assert other_accent not in sent[0]["html"]
    assert support in sent[0]["from"]
    assert firm["firm"] in sent[0]["from"]  # white-labelled, not Graphene's
    # The no-oracle rule, on the one thing that could carry a name: the copy is never personalised.
    assert firm["first_name"] not in sent[0]["html"]


@pytest.mark.asyncio
async def test_the_response_is_identical_whichever_portal_asked_and_whoever_asked(
    service_sessions, firm: dict[str, str], sent: list[dict[str, Any]], monkeypatch
) -> None:
    """THE SECURITY PROPERTY, restated after the change: choosing a brand must not become a way to
    ask the endpoint a question.

    The same fixed body on the staff host, on the client host, and for an address that exists on
    neither — so nothing an unauthenticated caller can see tells them whether the address exists or
    which side of the firm it belongs to. What varies is the email, which only its owner receives.
    Compared as serialised bytes rather than by ``==`` so a differently-SHAPED answer (an extra key,
    a reordered dict) fails too."""
    monkeypatch.setattr(environments, "core_workos_client", _MintingWorkOS)
    answers = [
        json.dumps(await _forgot(service_sessions, host, email), sort_keys=False)
        for host, email in (
            (firm["platform_host"], firm["email"]),
            (firm["client_host"], firm["email"]),
            (firm["platform_host"], f"nobody-{uuid.uuid4().hex[:8]}@verified.test"),
            (firm["client_host"], f"nobody-{uuid.uuid4().hex[:8]}@verified.test"),
        )
    ]
    assert answers == ['{"ok": true}'] * 4


@pytest.mark.asyncio
async def test_env_config_by_host_names_the_portal_a_host_is(
    service_sessions, firm: dict[str, str]
) -> None:
    """The fact the fix rests on, pinned on its own: the config read can tell the two hosts apart.
    It always could — the branding it returns is keyed on exactly this value — but it dropped the
    answer on the floor, which is why the caller had nothing to resolve a portal from and guessed.

    An unregistered host stays None (the ops/admin host, or local dev), which is the branch that
    keeps a Graphene-branded reset Graphene-branded."""
    async with service_sessions() as session:
        for key, expected in (("client_host", "client"), ("platform_host", "platform")):
            cfg = await environments.env_config_by_host(session, firm[key])
            assert cfg is not None
            assert cfg["portal"] == expected
            # …and the branding it serves really is that portal's, so the two cannot drift apart.
            assert cfg["branding"]["colors"]["light"]["primary"] == (
                PLATFORM_ACCENT if expected == "platform" else CLIENT_ACCENT
            )
        assert await environments.env_config_by_host(session, "admin.graphene.test") is None


@pytest.mark.asyncio
async def test_a_reset_from_the_firm_host_is_on_activity_and_never_the_inbox(
    service_sessions,
    firm: dict[str, str],
    sent: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    live_comms_ledger: None,
) -> None:
    """Forgot-password is transactional mail: it goes through ``send_email`` and the ledger, so
    Activity can show it, but it is NOT a notification — the bell is the ``audit.actions`` fan-out
    and this route never writes one. The screenshot of a Resend ``POST /emails`` that never
    appeared in the inbox is this path, not a missing bell.

    A send from the firm's own portal host carries that tenant, which is what the Activity feed
    scopes on.
    """
    monkeypatch.setattr(environments, "core_workos_client", lambda: _MintingWorkOS())
    assert await _forgot(service_sessions, firm["platform_host"], firm["email"]) == {"ok": True}
    assert len(sent) == 1

    async with service_sessions() as session:
        activity = await stream.fetch_messages(session, tenant_id=int(firm["tenant_id"]))
        notices = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM notifications.notifications "
                        "WHERE recipient_user_id = CAST(:u AS uuid)"
                    ),
                    {"u": firm["user_uuid"]},
                )
            ).scalar_one()
        )
    assert notices == 0
    assert len(activity["items"]) == 1
    row = activity["items"][0]
    assert row["template_code"] == "password_reset"
    assert row["event_type"] is None
    assert row["source"] == "app"
    assert row["recipient_kind"] == "external"
    assert row["to_address"] == firm["email"]
    assert row["status"] == "sent"


@pytest.mark.asyncio
async def test_a_reset_from_an_unknown_host_is_not_on_the_firm_activity_feed(
    service_sessions,
    firm: dict[str, str],
    sent: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    live_comms_ledger: None,
) -> None:
    """Local/ops host: ``tenant_id`` is None, so the row is Graphene's. The firm's Activity feed
    predicates on its own tenant and will not show it — which is why a reset posted from
    ``localhost`` (or the admin host) never appears there even when Resend delivered it.
    """
    monkeypatch.setattr(environments, "core_workos_client", lambda: _MintingWorkOS())
    assert await _forgot(service_sessions, "admin.graphene.test", firm["email"]) == {"ok": True}
    assert len(sent) == 1

    async with service_sessions() as session:
        activity = await stream.fetch_messages(session, tenant_id=int(firm["tenant_id"]))
        orphan = (
            (
                await session.execute(
                    text(
                        "SELECT tenant_id, template_code, recipient_kind FROM comms.messages "
                        "WHERE to_address = :addr AND template_code = 'password_reset'"
                    ),
                    {"addr": firm["email"]},
                )
            )
            .mappings()
            .one()
        )
    assert activity["items"] == []
    assert orphan["tenant_id"] is None
    assert orphan["recipient_kind"] == "external"
