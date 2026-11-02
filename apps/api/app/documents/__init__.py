"""Document management internals shared by the HTTP surface and the automation engine.

``app/routers/documents.py`` is the staff-facing HTTP surface; ``service.py`` here is the business
logic underneath it, extracted so an automation performs a client-facing document act by calling
the SAME function a person's request calls (docs/flows.md — the action executor's first rule).
"""
