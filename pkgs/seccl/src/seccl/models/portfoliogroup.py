"""PortfolioGroup models, generated from the vendored Postman collection.

Do not edit by hand: `moon run seccl:build-schemas && moon run seccl:generate-models`.
The types are inferred from 10 example payload(s), not from a published schema --
see pkgs/seccl/README.md for what that does and does not buy you.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class NominatedAccount(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account_id: Annotated[str, Field(alias="accountId")]
    fee_covered_accounts: Annotated[list[str], Field(alias="feeCoveredAccounts")]


class FeeStructures(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    adviser_fee_id: Annotated[str, Field(alias="adviserFeeId")]
    """
    observed values: 64f1a2b3c4d5e6f7a8b9c0d1
    """


class IntentDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: str | None = None
    """
    observed values: Active, Archived
    """
    platform_fee: Annotated[bool | None, Field(alias="platformFee")] = None
    adviser_fee: Annotated[bool | None, Field(alias="adviserFee")] = None
    fee_structures: Annotated[FeeStructures | None, Field(alias="feeStructures")] = None
    add_members: Annotated[list[str] | None, Field(alias="addMembers")] = None
    remove_members: Annotated[list[str] | None, Field(alias="removeMembers")] = None


class PortfolioGroup(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: str | None = None
    """
    observed values: 024H3, 64a1b2c3d4e5f6a7b8c9d0e1
    """
    firm_id: Annotated[str | None, Field(alias="firmId")] = None
    """
    observed values: FIRM001
    """
    group_intent: Annotated[str | None, Field(alias="groupIntent")] = None
    """
    observed values: Client Fee Group
    """
    name: str | None = None
    """
    observed values: Smith Family Group
    """
    update_id: Annotated[str | None, Field(alias="updateId")] = None
    """
    observed values: 64a1b2c3d4e5f6a7b8c9d0e2
    """
    intent_details: Annotated[IntentDetails | None, Field(alias="intentDetails")] = None


class PortfolioGroupCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    firm_id: Annotated[str, Field(alias="firmId")]
    name: str
    """
    observed values: Addams Family
    """
    node_id: Annotated[list[str], Field(alias="nodeId")]
    group_intent: Annotated[str, Field(alias="groupIntent")]
    """
    observed values: Client Fee Group
    """
    intent_details: Annotated[IntentDetails, Field(alias="intentDetails")]


class PortfolioGroupUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    update_id: Annotated[str, Field(alias="updateId")]
    nominated_accounts: Annotated[
        list[NominatedAccount] | None, Field(alias="nominatedAccounts")
    ] = None
    intent_details: Annotated[IntentDetails | None, Field(alias="intentDetails")] = None
    name: str | None = None
    """
    observed values: New name
    """
