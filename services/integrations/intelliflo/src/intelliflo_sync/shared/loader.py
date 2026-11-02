"""Dynamically load integration classes by provider name."""

import importlib
import logging

from intelliflo_sync.shared.base.integration import BaseIntegration

logger = logging.getLogger(__name__)


def load_integration(provider: str) -> type[BaseIntegration]:
    """
    Load the integration class for a given provider name.

    Convention:
      provider "intelliflo"    → providers/intelliflo/integration.py → IntellifloIntegration
      provider "chester_rose"  → providers/chester_rose/integration.py → ChesterRoseIntegration
    """
    module_path = f"intelliflo_sync.providers.{provider}.integration"
    class_name = "".join(word.title() for word in provider.split("_")) + "Integration"

    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        logger.debug("Loaded %s from %s", class_name, module_path)
        return cls
    except ImportError as exc:
        if module_path not in str(exc):
            raise ValueError(f"Provider module '{module_path}' failed to import: {exc}") from exc
        raise ValueError(
            f"No provider module found at '{module_path}'. Create providers/{provider}/integration.py."
        ) from exc
    except AttributeError as exc:
        raise ValueError(f"Provider module '{module_path}' has no class named '{class_name}'.") from exc
