-- 0075_provider_config: the vendor's per-connection reference values, on the connection.
--
-- `platform.providers` is already one row per upstream SOURCE -- for Morgan Lloyd, one Microsoft
-- (Dynamics) tenant per firm. The values Morgan Lloyd issues in their API Access Document belong
-- there and nowhere else, because they are properties OF THAT CONNECTION:
--
--   firm_id      the adviser firm as a Dynamics `accounts` GUID. The vendor asks for one value
--                under three field names -- `cam_financialadviser_Account` on Create Contact,
--                `accountId` on Create Adviser, `provider` on Create Investment / Create
--                Valuation -- calling all three "the Firm ID we provided you". Verified against
--                the live org: every synced contact carries it as `_cam_financialadviser_value`,
--                and it matches no row in `raw.contacts`, because it is an account and not a
--                person. It is NOT this row's `external_id`: that is the AZURE DIRECTORY, used
--                for the OAuth token URL and stamped on every record as `ml_tenant_id`.
--   scheme_type  }  Create Scheme's pairing. Option-set members with no documented labels, which
--   sipp_type    }  the vendor validates AS A PAIR ("Invalid Scheme and Sipp Type combination"),
--                   so a wrong half is not separately reportable.
--
-- WHY NOT ENV, which is where they were. Env is per-DEPLOYMENT and this platform is
-- multi-tenant with one Dynamics org per firm. One `MORGAN_LLOYD_SCHEME_TYPE` cannot hold two
-- firms' values, so the second firm onboarded would silently receive the first firm's -- into a
-- system with no delete. That is exactly the failure the blank-by-default refusal was written to
-- prevent, and a single env var quietly defeats it. Env remains the FALLBACK (the sync service
-- reads these as `book_writer`, which has no grant here), so nothing breaks on a deployment that
-- has not filled the column in.
--
-- jsonb rather than three columns: the set is provider-specific (Seccl and SEI have no analogue),
-- and `tenant_platforms.config` already establishes the pattern for provider-shaped settings with
-- a Python validator in front of it (`app/providers.py::validate_source_config`).

ALTER TABLE platform.providers
  ADD COLUMN config jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN platform.providers.config IS
  'Per-connection vendor reference values, validated by app/providers.py::validate_source_config. '
  'Morgan Lloyd: firm_id (the adviser firm as a Dynamics accounts GUID -- NOT external_id, which '
  'is the Azure directory), scheme_type, sipp_type. Env is the fallback; see '
  'docs/morgan-lloyd/README.md.';
