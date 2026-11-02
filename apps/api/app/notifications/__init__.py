"""In-app notifications — an event-driven notification centre across the three portals.

The sink is ``service.notify`` (one row per recipient in ``notifications.notifications``). Two
producers feed it, both service_role:

* the **tail** (``consumer.consume_once``) — a second cursor consumer of ``audit.actions`` (the
  first is ``flows.automation``), turning request-path actions into notifications per ``registry``;
* **domain producers** — direct ``notify`` calls at worker-side transitions the request-level
  action stream cannot see (a journey blocking, an AML hit), made in their existing service_role
  transaction.

A signed-in user only ever READS their own rows and marks them read (RLS + the router); every
WRITE is service_role. Per-user, per-category channel preferences gate the fan-out. The email
channel is a declared but INERT second delivery in v1 (the ``service`` seam is marked).
"""
