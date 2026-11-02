"""Database access for the integration engine."""

import json
import logging
from datetime import datetime

import psycopg2
import psycopg2.extras
from psycopg2 import sql

from intelliflo_sync.shared import config

S = config.SCHEMA_NAME


class _SafeJson(psycopg2.extras.Json):
    """Json adapter that handles dates, numpy scalars, and other non-standard types."""

    def dumps(self, obj):
        def _default(o):
            if hasattr(o, "isoformat"):
                return o.isoformat()
            if hasattr(o, "item"):  # numpy scalar → Python scalar
                return o.item()
            return str(o)

        return json.dumps(obj, default=_default)


logger = logging.getLogger(__name__)


def _get_connection():
    conn = psycopg2.connect(
        host=config.PG_HOST,
        port=config.PG_PORT,
        dbname=config.PG_DBNAME,
        user=config.PG_USER,
        password=config.PG_PASSWORD,
    )
    if config.PG_SET_ROLE:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(config.PG_SET_ROLE)))
        conn.commit()
    return conn


def upsert_integration(
    firm_id: str | None,
    platform: str,
    tenant_id: str,
    tenant_name: str | None,
    application_data: dict | None = None,
    enabled: bool | None = None,
    installed: bool | None = None,
    app_id: str | None = None,
) -> str:
    """Insert integration on install (as 'pending'), update installed/enabled on later events.

    `status` is the FE approval workflow (pending/approved/rejected) and is never changed here.
    """
    installed = installed if installed is not None else True
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            # platforms is unique on (firm_id, platform_name, tenant_id) and the install webhook doesn't
            # know the firm, so match the existing row on (platform_name, tenant_id) instead of ON CONFLICT.
            cur.execute(
                f"SELECT id, app_id FROM {S}.platforms WHERE platform_name = %s AND tenant_id = %s ORDER BY id LIMIT 1",
                (platform, tenant_id),
            )
            existing = cur.fetchone()
            if existing:
                recorded_app_id = existing[1]
                if app_id and recorded_app_id and app_id != recorded_app_id:
                    # The app id picks the group income statements are filed under; an event must not change it.
                    logger.warning(
                        "platform %s (tenant %s): ignoring app id %r from the event, %r is recorded — change it "
                        "deliberately if the app really changed",
                        existing[0],
                        tenant_id,
                        app_id,
                        recorded_app_id,
                    )
                    app_id = None
                cur.execute(
                    f"""
                    UPDATE {S}.platforms SET
                        tenant_name = %s,
                        firm_id = COALESCE(firm_id, %s),
                        app_id = COALESCE(%s, app_id),
                        installed = %s,
                        enabled = COALESCE(%s, enabled),
                        application_data = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id
                    """,
                    (
                        tenant_name,
                        firm_id,
                        app_id,
                        installed,
                        enabled,
                        psycopg2.extras.Json(application_data),
                        existing[0],
                    ),
                )
            else:
                cur.execute(
                    f"""
                    INSERT INTO {S}.platforms
                        (firm_id, platform_name, tenant_id, tenant_name, status, application_data,
                         installed, enabled, app_id)
                    VALUES (%s, %s, %s, %s, 'pending', %s, %s, COALESCE(%s, false), %s)
                    RETURNING id
                    """,
                    (
                        firm_id,
                        platform,
                        tenant_id,
                        tenant_name,
                        psycopg2.extras.Json(application_data),
                        installed,
                        enabled,
                        app_id,
                    ),
                )
            row = cur.fetchone()
        conn.commit()
        return str(row[0])
    finally:
        conn.close()


def fetch_all_integrations() -> list[dict]:
    """Return all integrations with installed, enabled, and status fields (no features)."""
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    i.id,
                    i.tenant_id,
                    i.tenant_name AS name,
                    i.platform_name AS provider,
                    i.installed,
                    i.enabled,
                    i.status
                FROM {S}.platforms i
                """
            )
            rows = cur.fetchall()
            logger.info("Found %d integration(s) in DB", len(rows))
            return [dict(r) for r in rows]
    finally:
        conn.close()


def fetch_integration_features(integration_id: str) -> list[str]:
    """Return enabled feature codes for an active integration."""
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT feature_code
                FROM {S}.platform_features
                WHERE platform_id = %s
                ORDER BY feature_code
                """,
                (integration_id,),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def has_successful_run_for_date(integration_id: str, feature: str, data_date: str) -> bool:
    """Return True if a successful job run for this integration+feature+data_date already exists."""
    date_obj = datetime.strptime(data_date, "%Y%m%d").date()
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT 1 FROM {S}.job_runs
                WHERE platform_id = %s
                  AND feature = %s
                  AND status = true
                  AND data_date = %s
                LIMIT 1
                """,
                (integration_id, feature, date_obj),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def record_job_run(
    integration_id: str,
    feature: str,
    status: bool,
    error_message: str | None = None,
    data: dict | None = None,
    data_date: str | None = None,
) -> None:
    """Insert a completed job run into {S}.job_runs."""
    date_obj = datetime.strptime(data_date, "%Y%m%d").date() if data_date else None
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {S}.job_runs (platform_id, feature, status, error_message, data, data_date)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    integration_id,
                    feature,
                    status,
                    error_message,
                    _SafeJson(data) if data else None,
                    date_obj,
                ),
            )
        conn.commit()
    finally:
        conn.close()
