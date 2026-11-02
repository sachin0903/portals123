#!/usr/bin/env python3
"""Render the app `.env` fragment for the off-Coolify staging/prod deploy.

The infra `app_stack` Ansible role assembles the container `.env` from two fragments:
this one (the app team's vars + secrets, owned in the GitHub Environment) and an
infra-owned fragment rendered from Ansible vault (DB DSNs, registry, S3 — the values
infra creates). Infra is assembled last and wins.

This reads VARS_JSON / SECRETS_JSON (set from `${{ toJSON(vars) }}` / `${{ toJSON(secrets) }}`
in deploy.yml), drops the infra-reserved keys and GitHub-internal ones, and writes
`KEY=VALUE` lines to stdout. Add a new env var by referencing `${NEW}` in
docker-compose.yaml and setting NEW in the GitHub Environment — no infra change.
"""

import json
import os
import sys

# Infra renders these from vault (app_stack/templates/env-infra.j2) — the GitHub
# Environment must NOT set them, and deploy-plumbing secrets are not app env.
RESERVED = {
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "BOOK_WRITER_DSN",
    "DATALAKE_WRITER_DSN",
    "DATALAKE_DATABASE_URL",
    "DATALAKE_CATALOG_DSN",
    "DATALAKE_OLTP_DSN",
    "S3_ENDPOINT_URL",
    "S3_ENDPOINT_HOST",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_FORCE_PATH_STYLE",
    "PORTALS_IMAGE_TAG",
    "APP_STAGE",
    "ANSIBLE_VAULT_PASSWORD",
    "INFRA_REPO_TOKEN",
    "REGISTRY_USERNAME",
    "REGISTRY_PASSWORD",
    "COOLIFY_DEPLOY_WEBHOOK",
    "COOLIFY_API_TOKEN",
    "COOLIFY_API_URL",
}


def _load(name):
    raw = os.environ.get(name, "").strip()
    return json.loads(raw) if raw else {}


def main():
    merged = {}
    merged.update(_load("VARS_JSON"))  # non-secret config
    merged.update(_load("SECRETS_JSON"))  # secrets override same-named vars

    lines = []
    for key, value in merged.items():
        if key in RESERVED or key.upper().startswith(("GITHUB_", "ACTIONS_")):
            continue
        if key.lower() == "github_token" or value is None:
            continue
        value = str(value)
        if "\n" in value:
            sys.stderr.write(
                f"warning: dropping {key} — multiline values aren't supported in .env "
                "(base64-encode it, as the SEI/Seccl PEM keys already are)\n"
            )
            continue
        lines.append(f"{key}={value}")

    sys.stdout.write("\n".join(lines) + ("\n" if lines else ""))


if __name__ == "__main__":
    main()
