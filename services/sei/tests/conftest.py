"""Test isolation.

``SeiSettings`` reads ``.env`` and the ambient environment by design, which means
an unguarded test would silently pick up whatever a developer has configured
locally — including the real SEI credentials and PGP key. Every test builds its
settings explicitly, so cut both sources off for the whole suite.
"""

from __future__ import annotations

import os

import pytest

from sei_sync.settings import SeiSettings


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [key for key in os.environ if key.startswith("SEI_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(SeiSettings.model_config, "env_file", None)
