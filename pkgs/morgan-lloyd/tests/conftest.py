import pytest

from morgan_lloyd._base import clear_token_cache


@pytest.fixture(autouse=True)
def _fresh_token_cache() -> None:
    """The token cache is process-wide (shared across client instances); tests
    construct clients with their own scripted token responses, so isolate them."""
    clear_token_cache()
