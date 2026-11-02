"""Local compose interpolates from the root ``.env`` even for db-up.

App secrets stay in ``apps/*/.env.local`` / ``services/*/.env``. Interpolations
here carry ``:-`` defaults; production ``docker-compose.yaml`` stays ``:?``.
"""

from __future__ import annotations

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.local.yaml"

# ``${NAME}`` with no default. ``$$`` is compose's escape. Comments are interpolated too.
_BARE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def test_every_interpolation_has_a_default() -> None:
    text = COMPOSE.read_text()
    unescaped = text.replace("$$", "")
    bares = _BARE.findall(unescaped)
    assert bares == [], (
        "docker-compose.local.yaml interpolates these with no default; "
        "`moon run api:db-up` then warns unless they sit in the root .env: "
        f"{bares}"
    )


def test_graphene_dev_is_external() -> None:
    """So `compose down` cannot detach a standalone Postgres. Create it first."""
    text = COMPOSE.read_text()
    assert "  graphene-dev:\n    external: true" in text


def test_api_loads_app_dotenv_and_does_not_blank_the_fernet_key() -> None:
    """A blank ``SECRETS_MASTER_KEY:`` in environment: would override env_file."""
    text = COMPOSE.read_text()
    assert "path: apps/api/.env.local" in text
    assert not re.search(r"^\s+SECRETS_MASTER_KEY:", text, re.MULTILINE)


def test_pipelines_bind_mount_book_sink() -> None:
    """Live package, not the image copy of the enablement query."""
    text = COMPOSE.read_text()
    assert text.count("./pkgs/book-sink:/app/pkgs/book-sink") >= 3
    source = (
        Path(__file__).resolve().parents[3] / "pkgs/book-sink/src/book_sink/tenants.py"
    ).read_text()
    assert "FROM platform.tenant_platforms" in source
    assert "connect.platforms" not in source
