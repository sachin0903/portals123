"""Sync-scope settings. The CRM connection itself is configured on the client
package (``MORGAN_LLOYD_*`` — see ``morgan_lloyd.settings``); these are the
extraction inputs the API cannot enumerate for us. Instantiate lazily —
model discovery imports every module under models/, which must succeed with
no env configured."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


class SyncSettings(BaseSettings):
    """Extraction inputs. All vars are ``ML_SYNC_*``."""

    model_config = SettingsConfigDict(env_prefix="ML_SYNC_", env_file=".env", extra="ignore")

    # The CRM has no "list schemes" endpoint: scheme-scoped pulls (contributions,
    # bank transactions) fan out over this comma-separated list of scheme
    # references (e.g. "SSAS/00297,SIPP/01234"). Blank ⇒ those models are no-ops.
    scheme_refs: str = ""
    # Adviser books pulled via cam_getadvisercontacts. Blank ⇒ no-op.
    adviser_emails: str = ""

    @property
    def scheme_ref_list(self) -> tuple[str, ...]:
        return _split_csv(self.scheme_refs)

    @property
    def adviser_email_list(self) -> tuple[str, ...]:
        return _split_csv(self.adviser_emails)
