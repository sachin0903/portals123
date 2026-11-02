"""The local anonymiser refuses anything that is not on this machine."""

from __future__ import annotations

import pytest

from scripts.anonymise_local import require_local_host


def test_localhost_is_accepted() -> None:
    assert (
        require_local_host("postgresql://postgres:postgres@127.0.0.1:5432/portals") == "127.0.0.1"
    )
    assert require_local_host("postgresql://postgres@localhost/portals") == "localhost"


def test_a_remote_host_is_refused() -> None:
    with pytest.raises(SystemExit, match="not on localhost"):
        require_local_host("postgresql://postgres:secret@db.internal:5432/portals")
    with pytest.raises(SystemExit, match="not on localhost"):
        require_local_host("postgresql://postgres@db/portals")
