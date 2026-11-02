"""Find-or-create anchors: one client, one model, one account — reused for ever.

**The CLIENT and its ACCOUNT are fresh every run; the MODEL is not.** That split is deliberate and
was a decision, not a default:

* A **client** is cheap and disposable on Seccl staging, and a fresh one each run buys real
  coverage — the create path is exercised every time rather than once against an empty firm, and
  the whole chain (create → accept terms → Active → money) is proved from nothing rather than
  proving that yesterday's client still works. Seccl has delete, so it is recoverable.
* A **model** belongs to the FIRM, not to a client. One per run would grow the firm's model list
  for ever with nothing to attach to, which is a leak with no upside — so it is found by name and
  created once.

The run is identified by `RUN_MARKER`, a timestamp stamped into the client's name, so a human
reading staging can see which run made what.

Discovery is from the API, never from local state. A committed id rots the moment somebody resets
staging, and a cache file makes a fresh clone behave differently from a warm one — the property
least wanted in a test that already depends on somebody else's environment. What IS cached is the
resolved id as a STRING, for the life of the process, which is what lets the fixtures stay
function-scoped (a session-scoped async fixture binds to one event loop and pytest-asyncio gives
each test its own).

The chain each anchor is found by:

* **client**   created fresh, named `ZZIntegration <RUN_MARKER>`. The surname field is
               `surname`, NOT `lastName` — `lastName` is not a field on a Seccl client at all and
               is silently ignored, which is the first thing a create teaches you.
* **model**    `name` over `GET /model/{firm}`, created once and reused. Discretionary, because
               that is the path carrying `investmentDecision` — the block a regulator asks about,
               and the half an advisory model does not exercise.
* **account**  created against this run's client with the model attached **at create time**,
               because that is the only time Seccl allows it (`AccountUpdate` has no
               `wrapperDetail`).

Nothing here is a fixture. They are plain functions taking a client, so the same discovery can be
used from a fixture, from a test, or from a one-off script without pytest in the room.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from seccl.client import SecclClient

#: Resolved anchor ids, for the life of the process. STRINGS only — never a client, never a
#: fixture. See the module docstring for why that distinction is load-bearing.
_CACHE: dict[str, str] = {}


def cached(key: str) -> str | None:
    return _CACHE.get(key)


def remember(key: str, value: str) -> str:
    _CACHE[key] = value
    return value


#: This run's identity, stamped into the client's surname.
#:
#: Computed ONCE at import so every fixture in the run agrees, which is the same job `_CACHE`
#: does for the resolved ids — two tests must not create two clients because they asked at
#: different seconds.
RUN_MARKER = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")

#: The client's name. `ZZ` so it sorts to the end of any staging list a human is reading, and
#: "Integration" so the reason it exists is legible without opening this file.
CLIENT_FIRST = "ZZIntegration"
CLIENT_LAST = f"Run{RUN_MARKER}"

#: The model is reused across runs, so its name is FIXED — it is found by it.
MODEL_NAME = "ZZIntegration Model"

#: The account is this run's, so its name carries the marker too. Nothing looks it up by name any
#: more; it is named for the human reading staging.
ACCOUNT_NAME = f"ZZIntegration Account {RUN_MARKER}"

#: The wrapper the anchor account opens as. GIA deliberately: it is the wrapper with the fewest
#: preconditions — an ISA carries subscription rules and a PENSION needs a client product — and
#: this suite is about the MONEY RAIL, not about wrapper eligibility.
ACCOUNT_WRAPPER = "GIA"


def _named(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def record_id(record: dict[str, Any]) -> str:
    """The id out of a Seccl record, whichever of its three spellings it used.

    `id`, `_id` and the resource-specific `clientId`/`accountId` all appear across the
    collection's saved responses. A create whose identifier cannot be read is the Morgan Lloyd
    scar in another vendor's clothes, so this looks for all of them rather than assuming one.
    """
    return _named(record, "id", "_id", "clientId", "accountId", "modelId")


async def find_client(api: SecclClient) -> dict[str, Any] | None:
    """The anchor client, by name.

    **`firstName` filters server-side and `lastName` does not** — verified live: `lastName=…`
    comes back with all 288 staging clients while `firstName=…` narrows correctly. So the query
    narrows on the half that works and the match is still made in full here, which is what makes
    the silently-ignored filter harmless rather than a wrong answer waiting to happen.
    """
    for row in await api.clients(firstName=CLIENT_FIRST):
        # `surname`, not `lastName`. `lastName` is not a field on a Seccl client at all — it is
        # simply ignored on the way in and absent on the way out, so a match on it never fires
        # and the anchor would be recreated on every run.
        if _named(row, "firstName") == CLIENT_FIRST and _named(row, "surname") == CLIENT_LAST:
            return row
    return None


async def ensure_client(api: SecclClient, node_id: str) -> str:
    """This run's client, created once PER RUN.

    Two things the vendor does not spell out, both learned from a refusal:

    * **`nodeId` is a LIST on a client and a SCALAR on an account.** Seccl's own asymmetry, and a
      list of one is the usual value. A scalar here is not a type error — it is looked up as a
      node id and fails as one.
    * **The node has to be REAL.** `"0"` is the id every example in the Postman collection uses
      and it does not exist on this staging firm, whose nodes are `21`, `49`, `40`, `9`… A
      missing node answers `404 DataNotFoundError "Node static data not found"`, which names the
      problem clearly enough — but only if `firmId` was sent, because without it the same call
      is a bare `500` with no body worth reading.

    The node is not decoration: it decides which models and fee structures are available to the
    client (`clients.firm_nodes`), which is why this takes one rather than defaulting.
    """
    if (hit := cached("client")) is not None:
        return hit
    created = await api.create_client(client_payload(node_id))
    identifier = record_id(created)
    assert identifier, (  # noqa: S101 - anchor contract
        f"created a client and could not read its id out of {sorted(created)}"
    )
    return remember("client", identifier)


def client_payload(node_id: str, *, suffix: str = "") -> dict[str, Any]:
    """The anchor client's `POST /client` body — **the vendor's own required set, not ours.**

    Seccl publishes no schema (the package is built from a Postman collection, and
    `pkgs/seccl/README.md` is explicit that "present in every example" is decent evidence and not
    a guarantee). But a create with a nearly-empty body answers with `fieldErrors`, and that IS
    the vendor speaking. Asked once, it named twelve:

        surname · language · email · emailVerified · mobileVerified · currency · taxDomicile
        nationality · addressDetail{.address1, .country, .postCode}

    Two of those the generated `ClientCreate` also marks required (`currency`, `taxDomicile`).
    **The other ten it marks OPTIONAL** — so the inference is not merely over-required in places
    (`KNOWN_OVER_REQUIRED`), it is UNDER-required in others, and the two errors have opposite
    consequences: an over-required field fails a read of a perfectly good record, an
    under-required one lets a create go out that the vendor refuses.

    Three shapes worth naming, each of which fails in its own unhelpful way:

    * **`surname`, not `lastName`.** There is no `lastName` on a Seccl client. It is not
      rejected — it is ignored, and the refusal you get is "Surname can't be blank".
    * **`nationality` is a STRING**, not a list. A list answers "Currently this country is not
      on our approved list", which reads as the code being wrong rather than the shape.
    * **`nodeId` is a LIST** (a scalar answers a bare `500`), and the node has to exist — the
      collection's own examples all use `"0"`, which this firm does not have.

    The email is `.invalid`, a reserved TLD that can never resolve — nothing here should ever be
    able to receive mail — and it carries `RUN_MARKER`, because Seccl registers an address to one
    client and refuses the second (`400 email: Email already registered`).
    """
    # `suffix` distinguishes a SECOND client made in the same run. The contract suite creates
    # one to observe the create path while the lifecycle creates its own, and without this they
    # collide on the run's email — which Seccl refuses, so the second caller fails on the first
    # caller's success.
    identity = f"{RUN_MARKER}{suffix}".lower()
    return {
        "clientType": "Individual",
        "nodeId": [node_id],
        "firstName": CLIENT_FIRST,
        "surname": f"{CLIENT_LAST}{suffix}",
        "title": "Mr",
        "dateOfBirth": "1970-01-01",
        "currency": "GBP",
        "language": "en",
        "nationality": "GB",
        "taxDomicile": "GB",
        # PER RUN, because **Seccl enforces email uniqueness across clients**
        # (`400 email: Email already registered`) — the same rule Morgan Lloyd applies to a NI
        # number, and the reason a fixed address makes a create path testable exactly once.
        "email": f"zzintegration.{identity}@example.invalid",
        "emailVerified": False,
        "mobileVerified": False,
        "addressDetail": {
            "address1": "1 Integration Way",
            "city": "Bristol",
            "postCode": "BS1 4XE",
            "country": "GB",
        },
    }


async def ensure_terms_accepted(api: SecclClient, client_id: str) -> str:
    """Accept the client's terms, which is **what makes an account live.**

    A created client is `Pending` with `termsAccepted: None`, and so is every account under them.
    A Pending account holds nothing: a payment in books no transaction, a withdrawal is refused
    `Insufficient funds for this transaction`, and the transaction feed stays empty — so the whole
    money rail is unreachable until this runs.

    The route is **`PUT /client` with `AmendTermsAccepted`**, not anything on the account.
    `PUT /account` with `{"updateAction": "AmendStatus", "updateData": {"status": "Active"}}` —
    which the collection documents and which is the obvious thing to reach for — is ACCEPTED on a
    Pending account (200, empty body) and changes nothing. An answer with no effect and no error
    is the worst shape a vendor can offer, and it is why this took a while to find: the status is
    a CONSEQUENCE of the client's acceptance, not a field to set.

    Accepting the client's terms is sufficient on its own. The client PRODUCT also carries
    `acceptedProviderTerms`/`acceptedProviderTermsDate` and the collection has a PATCH for it, but
    the account goes Active without it — and that PATCH is refused `Record updated by another
    user` immediately afterwards anyway, because accepting the client's terms bumps the product's
    `updateId` too. Left alone rather than retried in a loop: it is not needed for what this suite
    tests, and a concurrency retry against a real vendor is a thing to add when something needs it.

    Idempotent — a client already Active is left alone, so a re-run is a single read.
    """
    client = await api.client(client_id)
    if str(client.get("status") or "") == "Active":
        return "Active"
    await api.set_client_status(
        client_id,
        {
            "updateAction": "AmendTermsAccepted",
            "updateReason": "Integration suite: terms accepted",
            "updateData": {"termsAccepted": True},
        },
    )
    return str((await api.client(client_id)).get("status") or "")


async def find_model(api: SecclClient) -> dict[str, Any] | None:
    for row in await api.models():
        if _named(row, "name") == MODEL_NAME:
            return row
    return None


async def ensure_model(api: SecclClient, node_id: str) -> str:
    """The anchor MODEL, created once — discretionary, with two real assets.

    The allocation percentages are **fractions** (0.6 / 0.4, not 60 / 40). That is Seccl's
    convention for model allocations and for fee rates alike, and it is the trap
    `docs/provider-pipeline.md` records as `_allocation_lines`: sending 60 does not fail, it
    creates a model allocating 6000%.

    `investmentDecision` is a regulatory block, not metadata — it names the LEI and the individual
    who made the decision — and it is what makes this the discretionary path rather than the
    advisory one. The values here are obviously fake and that is fine on staging; they are the
    SHAPE being exercised.
    """
    if (hit := cached("model")) is not None:
        return hit
    found = await find_model(api)
    if found is not None:
        return remember("model", record_id(found))

    assets = await api.assets()
    tradable = [record_id(asset) for asset in assets if record_id(asset)]
    assert len(tradable) >= 2, (  # noqa: S101 - anchor contract
        f"the firm's asset universe has fewer than two assets ({len(tradable)}), so no model "
        "can be allocated. `GET /asset/{firm}` is what a model's allocations reference."
    )

    created = await api.create_model(
        {
            "name": MODEL_NAME,
            "modelType": "Discretionary",
            "riskRating": 3,
            "productType": ["GIA", "ISA"],
            "aim": "Exercises the model-assignment path for the integration suite.",
            "suspendRebalances": True,
            # Required, and the generated model does not say so — the same inference gap
            # `client_payload` documents at length.
            "vatable": False,
            "availableToNodeId": [node_id],
            # FRACTIONS. 0.6 + 0.4, not 60 + 40.
            "allocations": [
                {"assetId": tradable[0], "percentage": 0.6},
                {"assetId": tradable[1], "percentage": 0.4},
            ],
            "investmentDecision": {
                # An LEI is EXACTLY 20 characters and Seccl enforces it ("is too long (maximum
                # is 20 characters)"). Obviously fake, correctly shaped — the shape is what is
                # under test, and a wrong length fails on the length rather than on the path.
                "firmLei": "ZZINTEGRATION0000001",
                "firmCountry": "GB",
                "personId": "TN000001A",
                "personIdType": "NINO",
                "personCountry": "GB",
            },
        }
    )
    identifier = record_id(created)
    assert identifier, (  # noqa: S101 - anchor contract
        f"created a model and could not read its id out of {sorted(created)}"
    )
    return remember("model", identifier)


async def find_account(api: SecclClient, client_id: str) -> dict[str, Any] | None:
    for row in await api.accounts_for_client(client_id):
        if _named(row, "name") == ACCOUNT_NAME:
            return row
    return None


async def ensure_account(api: SecclClient, *, client_id: str, node_id: str, model_id: str) -> str:
    """The anchor ACCOUNT, created once, **with the model attached at create time**.

    `wrapperDetail.assetAllocationId` is on `AccountCreate` and NOT on `AccountUpdate`, so this is
    the only moment a model can be attached by naming it. Changing an account's model afterwards
    is `recurringOrders` for the money that has not arrived and a rebalance for the holdings that
    have — which means anything in our own book that writes `platform.accounts.model_id` and
    calls it done has changed our record and instructed nobody.
    """
    if (hit := cached("account")) is not None:
        return hit
    created = await api.create_account(
        {
            "firmId": api.firm_id,
            "clientId": client_id,
            "nodeId": node_id,
            "accountType": "Wrapper",
            "name": ACCOUNT_NAME,
            "status": "Active",
            "currency": "GBP",
            "wrapperDetail": {"wrapperType": ACCOUNT_WRAPPER, "assetAllocationId": model_id},
        }
    )
    identifier = record_id(created)
    assert identifier, (  # noqa: S101 - anchor contract
        f"created an account and could not read its id out of {sorted(created)}"
    )
    return remember("account", identifier)


async def update_id_of(api: SecclClient, account_id: str) -> str:
    """The account's current `updateId`, read fresh.

    **Seccl's PATCH is optimistically concurrent and this is the token.** It comes back on the GET
    as a top-level field, it changes on every write, and a stale one is refused — so it is read
    immediately before each PATCH rather than carried between tests. Caching it would make each
    test pass only when run in the order that happened to leave it valid, which is the kind of
    green nobody can trust.
    """
    account = await api.account(account_id)
    token = _named(account, "updateId")
    assert token, (  # noqa: S101 - anchor contract
        f"no updateId on account {account_id} (keys: {sorted(account)}) — every PATCH needs one."
    )
    return token
