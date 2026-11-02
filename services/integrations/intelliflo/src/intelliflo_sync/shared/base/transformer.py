"""Base transformer for normalising provider data to internal models."""

from abc import ABC, abstractmethod
from typing import Any


class BaseTransformer(ABC):
    @abstractmethod
    def transform(self, *args: Any, **kwargs: Any) -> Any: ...
