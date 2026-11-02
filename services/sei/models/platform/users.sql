/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.user_detail, ref.employee_users]
  tags: [sei, entity, conformed]
  description: >
    A firm's own STAFF at SEI — the employee users an adviser's Graphene login maps onto.
    Employee only: the same report carries 865 investors and 20 service accounts.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: key
    - not_null: firm_id
*/

-- The population behind `auth.merge_provider_user` (0123): each row either ATTACHES its
-- `entity_id` to the Graphene login that already holds its email, or becomes a disabled
-- placeholder so the firm can see that the person exists at its custodian.
--
-- ============================================================================================
-- THE SCOPE IS `USER_TYPE = 1`, AND THIS IS THE MOST IMPORTANT LINE IN THE MODEL.
-- ============================================================================================
--
-- `UserDetail` is 1,591 rows on the 2026-03-27 drop and only 706 of them are staff. `USER_TYPE`
-- is a Reference code set with exactly three members, and the split is:
--
--   1  Employee            706
--   2  End Client          865   <-- INVESTORS. Not staff. Excluded.
--   3  Data Integration     20   <-- SFTP/API machine logins. Not people. Excluded.
--
-- Publishing type 2 would put 865 investors into a firm's STAFF list, and worse: an End Client's
-- email plausibly matches the login that client already holds for the CLIENT portal, so the
-- attach rule would bind a custodian's staff-shaped id onto a client's identity and the firm's
-- adviser picker would offer its own clients as advisers. Type 3 are machines.
--
-- The scope is CORROBORATED rather than asserted. Of the 233 distinct `ACCOUNT_DSR_EMP_ID`
-- values in `ClientAccountLink` — the ids an account's servicing relationship actually names —
-- the 37 that appear in `UserDetail` at all are **37 of 37 Employee**. The type published here
-- is exactly the type an account names, and the other 196 are adviser PARTIES rather than SEI
-- logins (which is why a name match would be wrong 84% of the time, and why the mapping is by
-- email or by an operator, never by name).
--
-- DEPARTED STAFF ARE PUBLISHED. 470 of the 706 are already `USER_STATUS = 'N'`, and dropping
-- them would leave every account they advised attributed to nobody — a departed adviser is
-- still the name on that history. They land disabled like every other synced row, and
-- `is_active` says which is which so a picker can offer the current ones first.
--
-- ============================================================================================
-- WHY TWO SOURCES, WHICH WINS, AND WHICH ONE DECIDES WHAT REACHES `auth.users`.
-- ============================================================================================
--
-- MERGED, deliberately, and the merge is strictly more comprehensive than either side:
--
--   the DROP (`raw.user_detail`)          the API (`ref.employee_users`)
--   -----------------------------------   ------------------------------------------
--   the complete population               only what the credential can see
--   `USER_TYPE` -- THE ONLY DISCRIMINATOR no type field at all (verified against the
--     between Employee / End Client /       vendor's own `FetchFirmLevelEmployeeUsers`
--     Data Integration                      spec: crdNumber, emailId, firmId, firstName,
--   `USER_STATUS`, money-movement limit     lastName, loginUserName, middleName, phone,
--   one `USER_DISPLAY_NAME`, "Last, First"  state, uniqueUserId)
--   NO ADDRESS OF ANY KIND                **`emailId`** + a properly split name
--
-- **The API cannot be the sole source, and this is the reason.** Its response carries no
-- `userType`, so nothing in it distinguishes the firm's 546 employees from the 528 End Clients
-- and 4 service logins that share the same `UserDetail` file at firm 3102. The endpoint is
-- NAMED `firmemployeeusers` and its collection is `employeeUsersForFirmList`, which is a claim
-- that it returns employees — but it is only a claim, unverifiable from here (SEI allowlists the
-- API by source IP), and the estate's rule is to verify an external shape rather than assume it.
-- The cost of the assumption being wrong is investors in a firm's staff list and a client's own
-- portal login attached to a custodian's staff id. So the drop's `USER_TYPE` is the gate, and an
-- API user the drop labels type 2 or 3 is excluded whatever the endpoint chose to return.
--
-- **What the API decides is which rows reach `auth.users`.** The sink's `users` entity requires a
-- non-empty email (`book_sink.entities`), so only API-seen users are pushed — which is exactly
-- the intent: the email is the attach key, and a row without one can neither attach nor insert
-- (`auth.users.email` is NOT NULL). Drop-only employees are still RECORDED here, with a name and
-- a status and a NULL email, and counted by the merge as `no email at the provider`. A synthetic
-- address was considered and rejected: `auth.users.email` is what a login is addressed by and
-- what the send guard reads, so a plausible fabrication there is one careless invite away from
-- being treated as real.
--
-- ============================================================================================
-- TENANCY: THE FIRM AXIS ONLY, WHICH THE PREDICATE MUST BE TOLD.
-- ============================================================================================
--
-- A user belongs to a FIRM. `UserDetail` has a `FIRM_ID` and no subfirm and no DSR team, so
-- this does NOT carry the tenancy triple `platform.tenancy` gives the account-shaped entities —
-- and `book_sink.predicate.sei` would raise `Binder Error: Referenced column "subfirm_id" not
-- found` and abort the whole tenant's projection the moment an operator picked a subfirm, which
-- the admin pickers exist to do. `users` is therefore declared in `_SEI_NO_SUBFIRM_TABLES` and
-- `_SEI_NO_DSR_TABLES`, the same statement `firms` already makes, and `test_predicate.py` pins
-- it. This is the fourth time that shape has mattered.
--
-- `lineage` is the firm's own key rather than empty: a firm's staff are ITS staff, and an empty
-- path means global reference data that projects to everyone (`predicate.conformed`'s rule) —
-- which for people would show one firm another firm's employees.

WITH employees AS (
    SELECT
        u."ENTITY_ID"                                  AS entity_id,
        u."FIRM_ID"                                    AS firm_id,
        u."USER_DISPLAY_NAME"                          AS display_name,
        u."LOGIN_USER_ID"                              AS login_name,
        u."LOGIN_PHONE"                                AS phone,
        u."USER_STATUS"                                AS is_active,
        u."USER_CREATION_DATE"                         AS created_on,
        u."LAST_LOGIN_TIME"                            AS last_login_at,
        u."MONEY_MOVEMENT_LIMIT"                       AS money_movement_limit,
        u."ECX_ACCESS_FLAG"                            AS has_ecx_access,
        u.dt                                           AS dt,
        to_json(u)                                     AS data,
        -- The report is a DAILY SNAPSHOT and `raw.user_detail` merges every drop, so without
        -- this the relation holds one row per user per day and `unique: external_id` fails.
        -- Same window `platform.positions` takes over `raw.eod_positions`.
        row_number() OVER (PARTITION BY u."ENTITY_ID" ORDER BY u.dt DESC)  AS recency
      FROM raw.user_detail u
     WHERE u."ENTITY_ID" IS NOT NULL
       AND u."FIRM_ID" IS NOT NULL
       -- See the header. `USER_TYPE` is VARCHAR in the raw layer (a code, not arithmetic).
       -- '1' Employee ONLY: this excludes the 865 End Clients (investors) AND the 20 Data
       -- Integration rows, which are SFTP/API SERVICE LOGINS and not people at all — 4 of firm
       -- 3102's, one per integration. A machine account in a staff list is noise; a machine
       -- account that ATTACHED to a real login by sharing an ops mailbox would be worse.
       AND CAST(u."USER_TYPE" AS VARCHAR) = '1'
)
SELECT
    -- The conformed spine (docs/platform-model.md §3).
    e.entity_id                                        AS external_id,
    e.firm_id                                          AS key,
    [e.firm_id]                                        AS lineage,
    e.last_login_at::timestamp                         AS source_updated_at,
    e.dt                                               AS as_of_date,
    e.data                                             AS data,

    -- The SEI tenancy axis the predicate narrows on. Firm only — see the header.
    e.firm_id                                          AS firm_id,

    -- What `auth.merge_provider_user` reads. `email` is the attach key and is NULL whenever the
    -- API leg is off or has not seen this user; the merge counts that rather than guessing.
    a.email                                            AS email,

    -- The API's split name wins over the report's single field, because "Armasoiu, Maria" has to
    -- be parsed and `firstName`/`lastName` do not. The fallback splits on the FIRST comma:
    -- the report's form is "Last, First" (verified across the corpus), so everything before it
    -- is the surname and everything after is the given name. A row with no comma has no
    -- separable surname, so the whole string becomes the first name rather than being cut at a
    -- space — "de la Rue" is one surname and guessing would mangle it.
    coalesce(
        a.first_name,
        nullif(trim(regexp_extract(e.display_name, '^[^,]*,(.*)$', 1)), ''),
        nullif(trim(e.display_name), '')
    )                                                  AS first_name,
    coalesce(
        a.last_name,
        nullif(trim(regexp_extract(e.display_name, '^([^,]*),', 1)), '')
    )                                                  AS last_name,
    a.middle_name                                      AS middle_name,
    e.display_name                                     AS display_name,

    -- The report's phone is a mobile (`LOGIN_PHONE` — SEI sends security codes to it), and the
    -- API's `phone` is the same person's number from the other source. The report wins here
    -- because it is populated for the whole population where the API leg may be off.
    coalesce(nullif(trim(e.phone), ''), a.phone)      AS phone,

    coalesce(a.login_name, e.login_name)               AS login_name,
    a.crd_number                                       AS crd_number,

    -- SEI's own view of the person, carried for the picker rather than for `auth.users.enabled`
    -- — a synced row is disabled there whatever this says, because it has no WorkOS identity to
    -- authenticate with. 236 of the 706 are active.
    e.is_active                                        AS is_active,
    e.created_on                                       AS created_on,
    e.money_movement_limit                             AS money_movement_limit,
    e.has_ecx_access                                   AS has_ecx_access
  FROM employees e
  LEFT JOIN ref.employee_users a
    -- `entity_id` on both sides: the drop's `ENTITY_ID` and the API's `uniqueUserId` are the
    -- same identifier, and `ref.employee_users` stringifies its integer so this joins at all.
    ON CAST(a.entity_id AS VARCHAR) = CAST(e.entity_id AS VARCHAR)
 WHERE e.recency = 1
