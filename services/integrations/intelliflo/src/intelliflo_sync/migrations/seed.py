"""Local-only seed data for the integrations.

    APP_ENV=local intelliflo-seed [--config PATH] [--dry-run] [--reset]

Applies migrations/<provider>/seeds/*.sql in order, filling the parameters from the provider's
`local.yaml`. It exists so the integration can be tested locally against a real lake and real
Intelliflo credentials without going through the admin UI's approval flow. Seeds are idempotent:
re-running updates rather than duplicates.

It refuses to run unless APP_ENV=local AND the database host is local. The seeds directory is also
excluded from the Docker images (.dockerignore), and nothing in the scheduler or webhook imports this
module.

Seed files run as the `service_role` (the role the engine uses, which bypasses forced RLS). The
tables and columns they write must already exist — `apps/api/migrations` creates the `integrations`
schema; `platform.*` comes from the portals migrations too.
"""

import argparse
import json
import logging
import os
from pathlib import Path

import psycopg2
import yaml

from intelliflo_sync.providers.intelliflo import contract as iflo_contract
from intelliflo_sync.shared import config, contract

logger = logging.getLogger(__name__)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PROVIDER = "intelliflo"
SEEDS_DIR = Path(__file__).parent / PROVIDER / "seeds"
DEFAULT_CONFIG = SEEDS_DIR / "local.yaml"
REQUIRED_KEYS = ("tenant_id", "firm_id", "provider_id", "provider_name", "app_id")


def check_local() -> None:
    """Refuse to run anywhere but a local environment."""
    app_env = os.getenv("APP_ENV", "")
    if app_env != "local":
        raise SystemExit(f"Refusing to seed: APP_ENV is {app_env!r}, expected 'local'.")
    if config.PG_HOST not in LOCAL_HOSTS:
        raise SystemExit(f"Refusing to seed: PG_HOST is {config.PG_HOST!r}, expected one of {sorted(LOCAL_HOSTS)}.")


def load_params(path: Path) -> dict:
    """Read and validate local.yaml, returning the SQL parameters the seed files use."""
    if not path.exists():
        raise SystemExit(f"{path} not found — copy local.example.yaml to local.yaml and fill it in.")
    data = yaml.safe_load(path.read_text()) or {}
    missing = [k for k in REQUIRED_KEYS if not str(data.get(k) or "").strip()]
    if missing:
        raise SystemExit(f"{path.name} is missing: {', '.join(missing)}")

    accounts = [
        {k: str(a.get(k) or "").strip() for k in ("account_number", "policy_number", "plan_id")}
        for a in data.get("accounts") or []
    ]
    for a in accounts:
        if not all(a.values()) or any(v.startswith("REPLACE-") for v in a.values()):
            raise SystemExit(f"{path.name}: every account needs account_number, policy_number and plan_id (got {a})")

    group_id = str(data.get("group_id") or "").strip() or None
    return {
        "tenant_id": str(data["tenant_id"]).strip(),
        "tenant_name": str(data.get("tenant_name") or "Intelliflo (local seed)").strip(),
        "firm_id": str(data["firm_id"]).strip(),
        "provider_id": str(data["provider_id"]).strip(),
        "provider_name": str(data["provider_name"]).strip(),
        "app_id": str(data["app_id"]).strip(),
        "group_id": group_id,
        "graphene_tenant_id": int(data.get("graphene_tenant_id") or 3),
        "accounts": json.dumps(accounts),
    }


def seed_files(include: list[str] | None = None) -> list[Path]:
    """Seed files in order. Names starting with `_` (reset/undo scripts) only run when asked for."""
    if include:
        return [SEEDS_DIR / name for name in include]
    return sorted(p for p in SEEDS_DIR.glob("*.sql") if not p.name.startswith("_"))


def require_schema(cur) -> None:
    """Fail early, with the fix, if the tables/columns the seed writes are not there."""
    cur.execute(f"SET ROLE {config.PG_SET_ROLE}")
    gaps = contract.describe(contract.missing_columns(cur, iflo_contract.SEED))
    cur.execute("RESET ROLE")
    if gaps:
        raise SystemExit(
            "The database is missing: "
            + ", ".join(gaps)
            + ". Run apps/api's migrations first — that's what creates the integrations and platform tables."
        )


def apply_files(cur, files: list[Path], params: dict) -> None:
    cur.execute(f"SET ROLE {config.PG_SET_ROLE}")
    for path in files:
        cur.execute(path.read_text(), params)
        logger.info("applied %s", path.name)
    cur.execute("RESET ROLE")


def summary(cur, params: dict) -> None:
    cur.execute(f"SET ROLE {config.PG_SET_ROLE}")
    cur.execute(
        """
        SELECT id, firm_id, enabled, installed, status::text, provider_id, provider_name, app_id, group_id
        FROM integrations.platforms WHERE platform_name = 'intelliflo' AND tenant_id = %s
        """,
        (params["tenant_id"],),
    )
    row = cur.fetchone()
    logger.info("platform: %s", row)
    cur.execute(
        "SELECT count(*) FROM platform.links WHERE provider = 'intelliflo' AND data ->> 'seeded_local' = 'true'"
    )
    logger.info("seeded intelliflo account links: %s", cur.fetchone()[0])
    cur.execute("RESET ROLE")


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="integration-seed", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="path to local.yaml")
    parser.add_argument("--dry-run", action="store_true", help="apply everything, then roll back")
    parser.add_argument("--reset", action="store_true", help="remove what the seed created")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    check_local()

    if args.reset:
        params = load_params(args.config)
        files = seed_files(["_reset.sql"])
    else:
        params = load_params(args.config)
        files = seed_files()

    conn = psycopg2.connect(
        host=config.PG_HOST,
        port=config.PG_PORT,
        dbname=config.PG_DBNAME,
        user=config.PG_USER,
        password=config.PG_PASSWORD,
    )
    try:
        with conn.cursor() as cur:
            require_schema(cur)
            apply_files(cur, files, params)
            if not args.reset:
                summary(cur, params)
        if args.dry_run:
            conn.rollback()
            logger.info("dry run — rolled back, nothing was saved")
        else:
            conn.commit()
            logger.info("done")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run()
