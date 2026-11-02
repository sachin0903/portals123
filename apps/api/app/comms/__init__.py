"""Outbound communications: the record of what we sent, and what the provider did with it.

One module today (:mod:`app.comms.ledger`). The package exists because the ledger has two writers
that must never become three — ``app.email.send_email`` and the Resend webhook — and a package with
a stated boundary is what ``tests/test_email_send_guard.py`` scans to keep it that way.
"""
