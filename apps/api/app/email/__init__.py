"""Email — the whole feature, from the envelope identity to the bytes a client receives.

Ten modules, in the order a send passes through them:

* :mod:`~app.email.transport` — who the mail goes out AS (the firm's own envelope, an adviser's on
  their behalf), the non-production recipient guard, and the one call that reaches Resend.
* :mod:`~app.email.catalog` — the closed set of send codes, their categories and descriptions.
* :mod:`~app.email.defaults` — the shipped template for each code, built ONCE in two shapes: the
  send's HTML and the editor's own source.
* :mod:`~app.email.templates_service` — a firm's authored templates: versions, publish gating and
  the claim each one makes about what it is ABOUT (``EMAIL_SUBJECT_KINDS``).
* :mod:`~app.email.content` — what publish refuses (scaffold copy, an empty body) and what counts
  as renderable, which the SEND path also asks.
* :mod:`~app.email.render` — resolve a firm's published override for a code, or fall back to the
  shipped default.
* :mod:`~app.email.preview` — the same merge against sample values, for the editor. Never sends.
* :mod:`~app.email.shell` — the house chrome: masthead, button, footer and the card around them.
* :mod:`~app.email.document` — the client-safe HTML document, CSS folded inline.
* :mod:`~app.email.images` — inline image handling.

DELIBERATELY EMPTY OF CODE. Every one of these is imported by its own name, so nothing is
re-exported here: a package whose ``__init__`` republishes its submodules' symbols reads as one
module to every caller, and the next person adds to whichever file the import happens to name. It
also keeps the weight honest — ``from app.email import catalog`` pulls in a closed list of codes,
not httpx and the branding resolver, which is what importing the transport costs.

The Jinja environment stays in :mod:`app.render.jinja_engine`: it is shared with document rendering
and is not an email concern.
"""
