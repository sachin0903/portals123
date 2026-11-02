"""The ComplyCube adapters for the ``verify_identity`` and ``aml_check`` flow steps.

``app/flows/providers.py`` resolves both lazily. Neither calls ComplyCube from the step: the step
opens its pending ``crm.kyc_checks`` row, the adapter sets up the work, and the step reports
WAITING until ``app.complycube.routing.settle_portal_check`` releases it.

* **Identity** is work the CLIENT does. The adapter asks them for three photos
  (``app/idv/capture.py``) and moves nothing else; their submit starts the check.
* **AML** is work GRAPHENE does, and every AML step runs here. The adapter queues the screen
  (``app.complycube.screen``) and the worker runs it. A screen that cannot run goes to operations
  as a "Screen {client}" task. The firm never starts a paid screen itself.

Both write inside the step's own transaction, so a committed step always has its work raised.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from app.complycube import screen
from app.complycube.client import complycube_configured
from app.idv import capture

logger = logging.getLogger(__name__)

NAME = "complycube"


class ComplyCubeIdentityProvider:
    """Asks the client for the photos a ComplyCube identity check needs."""

    name = NAME

    async def start(
        self,
        *,
        tenant_id: int,
        case_id: int,
        step_id: str,
        subject_contact_id: int | None,
        session: Any = None,
        kyc_check_id: int | None = None,
    ) -> None:
        if not complycube_configured():
            # Reachable although preflight refuses an unconfigured provider at publish: a version
            # published while the key was set keeps running after it is removed. No photos are
            # asked for, so the client is offered the manual check instead.
            logger.warning(
                "complycube: not configured — case %s step %s falls back to a manual identity "
                "check",
                case_id,
                step_id,
            )
            return
        execution_id = None
        if session is not None:
            execution_id = (
                await session.execute(
                    text("SELECT execution_id FROM flows.cases WHERE id = :c"), {"c": case_id}
                )
            ).scalar_one_or_none()
        if session is None or subject_contact_id is None or execution_id is None:
            logger.warning(
                "complycube: no identity photos asked for case %s step %s (subject=%s, "
                "session=%s, execution=%s) — the step waits",
                case_id,
                step_id,
                subject_contact_id,
                session is not None,
                execution_id,
            )
            return
        await capture.raise_request(
            session, contact_id=int(subject_contact_id), case_id=case_id, step_key=step_id
        )
        # The photos are the client's to take, whoever the flow author assigned the step to.
        await session.execute(
            text(
                "UPDATE flows.case_steps SET assignee = 'client' "
                "WHERE case_id = :c AND step_key = :k AND participant_subject_id = :psid"
            ),
            {"c": case_id, "k": step_id, "psid": int(subject_contact_id)},
        )


class ComplyCubeAmlProvider:
    """Runs the client's AML screen by itself once the step starts (after identity passes). The kyc
    row stays pending until the result; a screen that cannot run goes to operations as a task
    (``app.complycube.screen``)."""

    name = NAME

    async def start(
        self,
        *,
        tenant_id: int,
        case_id: int,
        step_id: str,
        subject_contact_id: int | None,
        session: Any = None,
        kyc_check_id: int | None = None,
    ) -> None:
        if session is None or subject_contact_id is None:
            logger.warning(
                "complycube: no screen requested for case %s step %s (subject=%s, session=%s)",
                case_id,
                step_id,
                subject_contact_id,
                session is not None,
            )
            return
        await screen.enqueue(
            session,
            tenant_id=tenant_id,
            contact_id=int(subject_contact_id),
            kyc_check_id=kyc_check_id,
        )
