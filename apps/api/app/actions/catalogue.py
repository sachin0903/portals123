"""The domain-action catalogue. Code is the source of truth; a firm only enables rows.

A firm enables the action, not a source. The planner chooses the provider from
the book when the action runs. A book action (``kind='book'``) is a change to
the book itself. Naming an approver role holds it for a second person; an empty
list does not. ``client.setup`` is the onboarding plan: one action, composed of
the others.
"""

from __future__ import annotations

from dataclasses import dataclass

SOURCES: tuple[tuple[str, str], ...] = (
    ("sei", "SEI"),
    ("seccl", "Seccl"),
    ("morgan-lloyd", "Morgan Lloyd"),
    ("io", "IO"),
)

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("clients", "Clients"),
    ("accounts", "Accounts"),
    ("money_movement", "Money movement"),
    ("models", "Models"),
    ("fees", "Fees"),
    ("illustrations", "Illustrations"),
)

SOURCE_CODES: frozenset[str] = frozenset(code for code, _label in SOURCES)
CATEGORY_IDS: frozenset[str] = frozenset(code for code, _label in CATEGORIES)


@dataclass(frozen=True, slots=True)
class DomainAction:
    key: str
    name: str
    description: str
    category: str
    providers: tuple[str, ...]
    event_type: str
    subject_kinds: tuple[str, ...]
    one_open_per_subject: bool = False
    # provider — a call a firm enables per source. plan — composes other actions.
    # book — a mutation of our own rows, the reviews catalogue's shape.
    kind: str = "provider"
    # Approvers on this row hold the record save, even when the custodian send is off.
    holds_record: bool = False


def _a(
    key: str,
    name: str,
    description: str,
    category: str,
    providers: tuple[str, ...],
    event_type: str,
    subject_kinds: tuple[str, ...],
    *,
    one_open_per_subject: bool = False,
    kind: str = "provider",
    holds_record: bool = False,
) -> DomainAction:
    return DomainAction(
        key=key,
        name=name,
        description=description,
        category=category,
        providers=providers,
        event_type=event_type,
        subject_kinds=subject_kinds,
        one_open_per_subject=one_open_per_subject,
        kind=kind,
        holds_record=holds_record,
    )


_CUSTODIANS = ("sei", "seccl", "morgan-lloyd")
_CASH = ("sei", "seccl")

ACTIONS: tuple[DomainAction, ...] = (
    _a(
        "client.create",
        "Create client",
        "Create the client at the custodian or scheme administrator.",
        "clients",
        _CUSTODIANS,
        "client.created",
        ("contact",),
        one_open_per_subject=True,
    ),
    _a(
        "client.details",
        "Update client details",
        "Update the client's name, contact details, address, date of birth, and tax identity.",
        "clients",
        _CUSTODIANS,
        "client.details_updated",
        ("contact",),
    ),
    _a(
        "client.bank",
        "Update client bank account",
        "Write the bank account on the client, and send it to the custodian.",
        "clients",
        _CUSTODIANS,
        "client.bank_updated",
        ("contact",),
        holds_record=True,
    ),
    _a(
        "client.vulnerability",
        "Update client vulnerability",
        "Update whether the client is vulnerable, and the reasons recorded for that.",
        "clients",
        _CUSTODIANS,
        "client.vulnerability_updated",
        ("contact",),
    ),
    _a(
        "client.circumstances",
        "Update client circumstances",
        "Update pension details, employment, source of wealth, and tax relief.",
        "clients",
        _CUSTODIANS,
        "client.circumstances_updated",
        ("contact",),
    ),
    _a(
        "client.sync",
        "Sync client to Morgan Lloyd",
        "Create or resume the client, scheme, and policy at Morgan Lloyd.",
        "clients",
        ("morgan-lloyd",),
        "client.synced",
        ("contact",),
        one_open_per_subject=True,
    ),
    _a(
        "account.create",
        "Create account",
        "Open the account at its custodian, and at the scheme administrator when one is named.",
        "accounts",
        _CUSTODIANS,
        "account.created",
        ("account",),
        one_open_per_subject=True,
    ),
    _a(
        "account.pay_to",
        "Create pay-to profile",
        "Register the account's bank details at SEI once the opened account is no longer "
        "work in progress.",
        "accounts",
        ("sei",),
        "account.pay_to_created",
        ("account",),
    ),
    _a(
        "account.currencies",
        "Add allowable currencies",
        "Permit the firm's other currencies on an account SEI has already opened.",
        "accounts",
        ("sei",),
        "account.currencies_added",
        ("account",),
    ),
    _a(
        "account.details",
        "Update account details",
        "Update the account's name. Currency is set when the account is opened.",
        "accounts",
        _CASH,
        "account.details_updated",
        ("account",),
    ),
    _a(
        "account.bank",
        "Update account bank details",
        "Write the account's own bank account, used when it differs from the client's, "
        "and send it to the custodian.",
        "accounts",
        _CASH,
        "account.bank_updated",
        ("account",),
        holds_record=True,
    ),
    _a(
        "beneficiary.replace",
        "Update beneficiaries",
        "Replace the beneficiaries recorded at the scheme administrator.",
        "accounts",
        ("morgan-lloyd",),
        "account.beneficiaries_updated",
        ("account",),
    ),
    _a(
        "transaction.payment",
        "Payment",
        "Take a payment or contribution into the account — ISA and SIPP included.",
        "money_movement",
        _CUSTODIANS,
        "payment.one_off_instructed",
        ("account", "contribution"),
    ),
    _a(
        "payment.regular",
        "Regular payment",
        "Set up or change a regular payment into the account.",
        "money_movement",
        _CASH,
        "payment.regular_instructed",
        ("account",),
    ),
    _a(
        "transaction.withdrawal",
        "Withdrawal",
        "Pay a single withdrawal out of the account.",
        "money_movement",
        _CUSTODIANS,
        "withdrawal.one_off_instructed",
        ("account",),
    ),
    _a(
        "withdrawal.regular",
        "Regular withdrawal",
        "Set up or change a regular withdrawal from the account.",
        "money_movement",
        _CASH,
        "withdrawal.regular_instructed",
        ("account",),
    ),
    _a(
        "transaction.transfer",
        "Create transfer",
        "Instruct a transfer in or out of the account.",
        "money_movement",
        _CUSTODIANS,
        "transfer.created",
        ("account",),
    ),
    _a(
        "contribution.lodge",
        "Lodge contribution",
        "Tell the custodian to expect a contribution.",
        "money_movement",
        _CASH,
        "contribution.lodged",
        ("contribution",),
    ),
    _a(
        "contribution.record",
        "Record contribution",
        "Record a SIPP contribution at the scheme administrator. This does not move cash.",
        "money_movement",
        ("morgan-lloyd",),
        "contribution.recorded",
        ("contribution",),
        one_open_per_subject=True,
    ),
    _a(
        "transfer.record",
        "Record transfer",
        "Record a SIPP transfer at the scheme administrator.",
        "money_movement",
        ("morgan-lloyd",),
        "transfer.recorded",
        ("transfer",),
        one_open_per_subject=True,
    ),
    _a(
        "drawdown.start",
        "Start drawdown",
        "Start drawdown on a SIPP.",
        "money_movement",
        _CUSTODIANS,
        "drawdown.started",
        ("account",),
    ),
    _a(
        "trade.place",
        "Buy or sell",
        "Place a buy or sell on the account.",
        "money_movement",
        _CASH,
        "trade.placed",
        ("account",),
    ),
    _a(
        "model.attach",
        "Change model",
        "Set or change the account's investment model, and push the mandate to the custodian. "
        "Approvers here hold the change until a second person agrees.",
        "models",
        _CASH,
        "account.model_updated",
        ("account",),
        holds_record=True,
    ),
    _a(
        "model.remove",
        "Remove model",
        "Remove the account's investment model.",
        "models",
        ("sei",),
        "account.model_removed",
        ("account",),
    ),
    _a(
        "fee.attach",
        "Change fee packages",
        "Set the account's fee packages, and attach them at the custodian. "
        "Approvers here hold the change until a second person agrees.",
        "fees",
        _CASH,
        "account.fees_updated",
        ("account",),
        holds_record=True,
    ),
    _a(
        "transaction.fee",
        "Ad-hoc fee",
        "Charge an ad-hoc fee on the account.",
        "fees",
        _CASH,
        "fee.charged",
        ("account",),
    ),
    _a(
        "illustration.generate",
        "Generate illustration",
        "Generate an illustration for the account.",
        "illustrations",
        ("seccl", "morgan-lloyd"),
        "illustration.generated",
        ("account",),
    ),
    _a(
        "client.setup",
        "Set up client and accounts",
        "Onboarding plan: client, accounts, and whatever money, model, or fees were captured.",
        "clients",
        (),
        "client.setup_completed",
        ("account", "contact"),
        kind="plan",
    ),
    # Book actions — a change to the book. The door always exists. Approver roles
    # are the four-eye switch, and the description is the sentence shown beside it.
    # None of these is listed in settings any more: the bank saves are ``client.bank`` /
    # ``account.bank``, the model and fee saves are ``model.attach`` / ``fee.attach`` (one row
    # holds the record and pushes to the custodian). The keys stay because the doors hold on
    # them and run history carries them.
    _a(
        "contact.bank_details_changed",
        "Change a client's bank details",
        "A second person must approve before a client's bank details change.",
        "clients",
        (),
        "client.bank_account_updated",
        ("contact",),
        one_open_per_subject=True,
        kind="book",
    ),
    _a(
        "account.bank_details_changed",
        "Change an account's bank details",
        "A second person must approve before an account's own bank details change.",
        "accounts",
        (),
        "account.bank_accounts_updated",
        ("account",),
        one_open_per_subject=True,
        kind="book",
    ),
    _a(
        "account.model_changed",
        "Change model",
        "A second person must approve before an account's investment model changes.",
        "models",
        (),
        "account.model_updated",
        ("account",),
        one_open_per_subject=True,
        kind="book",
    ),
    _a(
        "account.fee_packages_changed",
        "Change fee packages",
        "A second person must approve before an account's fee packages change.",
        "fees",
        (),
        "account.fee_packages_updated",
        ("account",),
        one_open_per_subject=True,
        kind="book",
    ),
)

BY_KEY: dict[str, DomainAction] = {action.key: action for action in ACTIONS}


def get(key: str) -> DomainAction | None:
    return BY_KEY.get(key)


def implements(action: DomainAction, provider: str) -> bool:
    return provider in action.providers


# The bank saves are configured on ``client.bank`` and ``account.bank``. The book
# keys stay so the doors and existing holds still record them; settings does not
# list them again. ``contribution.lodge`` is the internal lodge leaf that
# ``transaction.payment`` calls for a contribution source — firms configure the
# policy on ``transaction.payment``, but the key stays here so run history resolves it.
_UNLISTED = frozenset(
    {
        "contact.bank_details_changed",
        "account.bank_details_changed",
        # Held on by the model / fee-packages doors; configured on `model.attach` / `fee.attach`.
        "account.model_changed",
        "account.fee_packages_changed",
        "contribution.lodge",
        # SEI-internal sub-steps of `account.create` (a pay-to profile and the allowable
        # currencies). They run as children of account opening, not as separately configurable
        # firm actions, so they are not listed in the policy editor.
        "account.pay_to",
        "account.currencies",
    }
)


def settings_rows() -> list[DomainAction]:
    """Every action firm settings lists. The bank book keys are not among them."""
    return [action for action in ACTIONS if action.key not in _UNLISTED]


_PROVIDER_LABELS = dict(SOURCES)

# The onboarding plan has no providers of its own. These are the ones its children call.
_SETUP_PROVIDERS = ("seccl", "sei", "morgan-lloyd")


# What the run reads. The settings page shows this. It does not collect a second copy.
READS: dict[str, str] = {
    "client.create": "The client record, and the firm's Seccl node.",
    "client.details": "The client's name, email, phone, address, date of birth, and tax identity.",
    "client.bank": "The bank account on the client.",
    "client.vulnerability": "The vulnerability flag, reasons, and notes on the client.",
    "client.circumstances": "Pension details, employment, source of wealth, and tax relief.",
    "client.sync": "The client, and each pension account's scheme.",
    "account.create": "The account, its product, and — when set — the model and scheme product.",
    "account.pay_to": "The bank account already on the client or the account.",
    "account.details": "The account's name. Currency is set when the account is opened.",
    "account.bank": "The bank account on the account.",
    "transaction.payment": (
        "A payment or contribution booked on the account, or the opening cash deposit."
    ),
    "payment.regular": "The standing amount recorded on the account.",
    "transaction.withdrawal": "The withdrawal already booked on the account.",
    "withdrawal.regular": "The standing withdrawal recorded on the account.",
    "transaction.transfer": "The transfer already raised on the account.",
    "contribution.lodge": "The contribution and its payment expectation.",
    "contribution.record": "The contribution, for a SEI SIPP Morgan Lloyd administers.",
    "transfer.record": "The transfer, for a SEI SIPP Morgan Lloyd administers.",
    "model.attach": (
        "The account's model. Seccl receives it when the account is opened. "
        "SEI is assigned it once the account is Open, on the variant for the model's type."
    ),
    "fee.attach": "The adviser fee package assigned to the account.",
    "transaction.fee": "The ad-hoc fee already booked on the account.",
    "illustration.generate": "The account's product and the client's Seccl id.",
    "client.setup": (
        "The application: each person and every account they hold, "
        "and whatever money each account captured."
    ),
}


def reads(action: DomainAction) -> str:
    """The book row this action sends. Empty when the action has nothing further to say."""
    return READS.get(action.key, "")


def provider_tags(action: DomainAction) -> list[dict[str, str]]:
    """The providers a row shows. A plan shows the providers its children can call."""
    codes = action.providers or (_SETUP_PROVIDERS if action.key == "client.setup" else ())
    return [{"code": code, "label": _PROVIDER_LABELS.get(code, code)} for code in codes]


def onboarding_plan() -> list[dict[str, object]]:
    """What ``client.setup`` expands into. Optional steps depend on the application."""
    return [
        {
            "provider": "seccl",
            "label": "Seccl",
            "steps": [
                {
                    "key": "client.create",
                    "name": "Create client",
                    "detail": (
                        "Identity, plus bank account and vulnerability when the record holds them."
                    ),
                },
                {
                    "key": "account.create",
                    "name": "Create account",
                    "detail": "GIA, ISA, or JISA. A pension waits until a scheme product is named.",
                },
                {
                    "key": "transaction.payment",
                    "name": "Payment",
                    "detail": "When the application captured a payment.",
                },
                {
                    "key": "payment.regular",
                    "name": "Regular payment",
                    "detail": "When the application captured one.",
                },
                {
                    "key": "model.attach",
                    "name": "Change model",
                    "detail": "When the application captured a model.",
                },
                {
                    "key": "fee.attach",
                    "name": "Change fee packages",
                    "detail": "When the application captured a fee package.",
                },
            ],
        },
        {
            "provider": "morgan-lloyd",
            "label": "Morgan Lloyd",
            "steps": [
                {
                    "key": "client.sync",
                    "name": "Sync client",
                    "detail": "Client, scheme, policy, beneficiaries, and circumstances.",
                },
            ],
        },
        {
            "provider": "sei",
            "label": "SEI",
            "steps": [
                {
                    "key": "account.create",
                    "name": "Enrol client and account",
                    "detail": (
                        "One SEI call opens every client and every account on the "
                        "application. It waits for the firm's template, team, and "
                        "classification ids. This step finishes when those ids are stored."
                    ),
                },
                {
                    "key": "account.pay_to",
                    "name": "Create pay-to profile",
                    "detail": (
                        "After the account is Open. A number on its own is still work in "
                        "progress, and SEI refuses a pay-to until that clears. No bank "
                        "details skips this step."
                    ),
                },
                {
                    "key": "account.currencies",
                    "name": "Add allowable currencies",
                    "detail": (
                        "After the pay-to. The firm's other currencies, one code at a "
                        "time. A code SEI will not add is left off."
                    ),
                },
            ],
        },
    ]
