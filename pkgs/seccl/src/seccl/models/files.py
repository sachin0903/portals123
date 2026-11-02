"""File models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 14 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class File(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    """
    observed values: 16-08-2022-1660640202083
    """
    url: str


class FileCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str
    firm_id: Annotated[str, Field(alias="firmId")]
    type: str | None = None
    """
    observed values: GENERATE_ILLUSTRATION_DOC
    """
    file_type: Annotated[str | None, Field(alias="fileType")] = None
    """
    observed values: ILLUSTRATION
    """
    client_id: Annotated[str | None, Field(alias="clientId")] = None
