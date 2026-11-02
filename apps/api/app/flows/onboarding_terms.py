"""The client onboarding terms declarations — ported from the legacy ``onboarding.terms`` seed
(platform-main ``pkgs/db/seed.sql``, client audience). Each is an HTML declaration the client
in-app and acknowledges (checkbox-now; an e-signature step can supersede later).

The ``scope`` on each mirrors the legacy selection rules (``onboarding-terms.ts``): audience is
always ``client`` here; ``product``/``transfer`` scoping is realised in the flow as *conditional
accept-terms steps* (General/Platform always; ISA when an ISA account is present; Transfer when
an inbound transfer is attached). Kept as data so a firm can edit the copy.
"""

from __future__ import annotations

from typing import Any

_GENERAL = (
    "<ol>"
    "<li>I confirm that I have read and understood the Terms and I agree to be bound by them.</li>"
    "<li>I confirm that I have not been provided with any advice under the terms of the Financial "
    "Services and Markets Act 2000 by the platform provider.</li>"
    "<li>I confirm that I have read the Privacy Notice contained in the Client Terms Document and "
    "give my permission for my personal data to be processed as described.</li>"
    "<li>I have received, and been given sufficient time to read and understand, all the necessary "
    "documentation applicable to this application, including the Client Terms, the Platform Client "
    "Terms (including Custody Terms), the Fee Schedule and the Privacy Policy.</li>"
    "<li>I have agreed to the deduction of charges as detailed in the Adviser Charges section of "
    "this application.</li>"
    "<li>I declare that this application has been completed to the best of my knowledge and "
    "belief.</li>"
    "</ol>"
)

_TRANSFER = (
    "<ol>"
    "<li>Please take this authority to transfer my entire holdings to my account held with the "
    "platform.</li>"
    "<li>By acknowledging below, I authorise the current provider or ISA manager to provide the "
    "platform with any information they may require in respect of this transfer.</li>"
    "<li>Either transfer my holdings to the platform or liquidate the assets within my account "
    "with immediate effect and forward the proceeds to the platform.</li>"
    "<li>Stop collecting my regular savings with immediate effect if any are currently being "
    "collected.</li>"
    "</ol>"
)

_ISA = (
    "<ol>"
    "<li>This will apply for ISAs being opened now or in the future.</li>"
    "<li>I apply to subscribe to a Stocks and Shares ISA for the current tax year and each "
    "subsequent tax year until it is closed.</li>"
    "<li>I confirm that all subscriptions made, and to be made, belong to me; that I have not "
    "subscribed, and will not subscribe, to more than the overall ISA subscription limit in the "
    "same tax year; and that I am resident in the United Kingdom for tax purposes (or otherwise "
    "qualify).</li>"
    "<li>I will inform the platform if I cease to be resident or to qualify.</li>"
    "<li>I have received the Platform Terms for the ISA I am applying to subscribe to and agree to "
    "them.</li>"
    "<li>I am 18 years of age or over.</li>"
    "<li>I authorise the platform to hold my subscriptions and any income earned on those "
    "subscriptions and to claim any relief from tax on my behalf.</li>"
    "</ol>"
)

_PLATFORM = "<p>I have read and agree to the platform terms.</p>"


# code -> {name, content (HTML), scope}. `scope` documents the legacy selection intent; the flow's
# conditional accept-terms steps realise it.
TERMS: dict[str, dict[str, Any]] = {
    "general_declaration": {
        "name": "General declaration",
        "content": _GENERAL,
        "scope": {"always": True},
    },
    "transfer_declaration": {
        "name": "Transfer declaration",
        "content": _TRANSFER,
        "scope": {"when": "has_transfer"},
    },
    "isa_declaration": {
        "name": "ISA declaration",
        "content": _ISA,
        "scope": {"when_product": "ISA"},
    },
    "platform_terms": {
        "name": "Platform terms",
        "content": _PLATFORM,
        "scope": {"always": True},
    },
}
