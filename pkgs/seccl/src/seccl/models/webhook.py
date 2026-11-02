"""Webhook models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 10 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Webhook(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = None
    url: str | None = None
    """
    observed values: https://example.com/hook
    """
    types: list[str] | None = None
    enabled: bool | None = None


class WebhookCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    url: str
    """
    observed values: https://example.com/hook
    """
    enabled: bool
    secret: str
    """
    observed values: ahfuirwhfueirdhuoe
    """
    types: list[str]


class WebhookUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    url: str | None = None
    """
    observed values: <your webhook url>, https://example.com/new-hook
    """
    types: list[str] | None = None
    secret: str | None = None
    """
    observed values: <your secret>, dshu;[]ewhuifew56533he
    """
    enabled: bool | None = None
