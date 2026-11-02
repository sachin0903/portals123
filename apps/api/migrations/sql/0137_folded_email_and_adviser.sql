-- ============================================================================
-- 0137_folded_email_and_adviser.sql -- store the email folded (and drop the index that was
-- working around not doing so), and split `is_adviser` out of `type`.
--
-- ---------------------------------------------------------------------------
-- 1. THE EMAIL IS STORED LOWERCASE, SO THE ORIGINAL UNIQUE KEY IS ENOUGH.
-- ---------------------------------------------------------------------------
--
-- 0124 added `users_tenant_email_ci_key ON (tenant_id, lower(email))` and deliberately kept the
-- stored value's case. That was the wrong half of the trade: it made the schema carry a second
-- index, an expression conflict target at every upsert, and a `lower()` on both sides of every
-- comparison — all to preserve capitalisation that nothing reads.
--
-- An address is case-insensitive in every way that matters here. RFC 5321 §2.4 leaves the local
-- part to the receiving server and every mail provider in practice folds it; more to the point
-- WorkOS is the authority for this value and normalises it itself, so storing mixed case meant
-- holding a spelling the identity provider does not. Folding on write makes
-- `users_tenant_email_key UNIQUE (tenant_id, email)` — which has existed since 0001 — do the
-- whole job, and every `ON CONFLICT (tenant_id, email)` in the estate becomes correct again
-- rather than needing to name an expression.
--
-- **The CHECK is the enforcement, not a comment.** Five paths write this column
-- (`provisioning.provision_member`, `provisioning.sync_tenant`'s insert and update,
-- `events.py`'s insert and update) and a sixth would forget, so the constraint is what makes
-- "we store it folded" true instead of aspirational: a path that does not normalise FAILS LOUDLY
-- at the boundary rather than silently creating a second login for one person, which is exactly
-- the defect 0124 found in the invite flow. `provisioning.fold_email` is the one implementation
-- and `tests/test_user_nature.py` pins that every write path routes through it.
--
-- A trigger was considered and rejected: it would fix writes and leave every READ comparing a
-- mixed-case value silently missing, so the application has to normalise regardless — and given
-- that, a constraint that fails is more honest than a trigger that hides.
--
-- ---------------------------------------------------------------------------
-- 2. `is_adviser` COMES OUT OF `type`, BECAUSE IT IS NOT A PEER OF THE OTHER TWO.
-- ---------------------------------------------------------------------------
--
-- 0124 made `type text[]` with members `client | staff | adviser`. Modelling adviser as a third
-- member is wrong in a way that shows up immediately: it makes `{client, adviser}` expressible —
-- an adviser who is not staff — and there is no such person. `client` and `staff` say WHICH SIDE
-- of the firm somebody is on, and they genuinely compound (a firm's adviser is commonly also a
-- client of the firm). Advising is a capability WITHIN staff, one level down.
--
-- So `type` keeps the two that are peers and `is_adviser` becomes a boolean whose dependency the
-- schema states: `is_adviser` implies `staff`. The nonsense is now unrepresentable rather than
-- merely discouraged, and the control it drives is a switch rather than a set editor.
--
-- **AND IT CANNOT BE DERIVED FROM ANY CUSTODIAN, WHICH IS MEASURED, NOT ASSUMED.**
--
--   * **SEI.** `Reference` carries a `DSR_TYPE_NAME` set with SEVEN 'Adviser' members, so the
--     vocabulary exists — but no account uses one. Across the whole 2026-03-27
--     `ClientAccountLink`, the DSR slots actually filled are Fee Receiving IFA Firm (199 distinct
--     employee ids, only 15 of them SEI logins), Client Service Team (17/16), IFA Firm (15/4),
--     Legal Entity (4/4), Investment Manager (2/2), Model Manager Team (1/1) and Service Team
--     (1/1). 'Adviser': **zero**. The 199 non-login ids are adviser PARTIES rather than people
--     with SEI accounts, which is the same population as the 196 of 233 that never appear in
--     `UserDetail`.
--   * **Seccl.** `userType` is `Machine | User` and nothing else; access there is the NODE TREE
--     rather than a person link, so a Seccl "adviser" is a node.
--   * **Morgan Lloyd.** One `cam_FinancialAdviserContact` for the whole firm.
--
-- So no feed can propose this and an operator states it. That is what the members Edit drawer is
-- for, and it is why the adviser profile fields (0118) sit behind the same switch.
-- ============================================================================

-- DDL AS `migrator`, DML AS `service_role`, and the two must not be interleaved: `service_role`
-- is not the OWNER of `auth.users` (migrator is), so an `ALTER TABLE` inside the elevated block
-- fails with "must be owner of table users" — while a bare `UPDATE` outside it reads zero rows
-- under RLS and succeeds, which is the silent trap 0042, 0059 and 0118 each record.
ALTER TABLE auth.users
  ADD COLUMN IF NOT EXISTS is_adviser boolean NOT NULL DEFAULT false;

SET LOCAL ROLE service_role;

-- REFUSE BEFORE FOLDING, because collisions ARE possible and this migration's own header explains
-- why: `provision_member` upserts `ON CONFLICT (tenant_id, email)`, which does not match a
-- differently-cased address, so it MINTED A SECOND LOGIN rather than being idempotent. A tenant
-- holding `A.Smith@firm.test` and `a.smith@firm.test` is therefore an expected state, and folding
-- both onto one value violates `users_tenant_email_key` and aborts the upgrade with a message
-- naming only the constraint.
--
-- An earlier cut of this file argued no collision was possible because `users_tenant_email_ci_key`
-- had been enforcing it "since 0124". That index does not exist on any upgrade path: it was
-- created by a migration that was dropped when this branch's identity work was reconciled onto
-- main's `0121_user_types`, which does not create one. The argument outlived its premise.
--
-- Two logins are two identities, so this REFUSES and names them rather than choosing which
-- survives: merging them decides which person keeps their history, and renaming one invents an
-- address, which is the fabrication `auth.users.email` must never hold. An operator merges the
-- pair and re-runs.
DO $$
DECLARE
  collisions text;
BEGIN
  SELECT string_agg(format('tenant %s: %s', tenant_id, folded), '; ' ORDER BY tenant_id, folded)
    INTO collisions
    FROM (
      SELECT tenant_id, lower(email) AS folded
        FROM auth.users
       WHERE email IS NOT NULL AND deleted_at IS NULL
       GROUP BY tenant_id, lower(email)
      HAVING count(*) > 1
    ) AS dupes;
  IF collisions IS NOT NULL THEN
    RAISE EXCEPTION
      'auth.users holds case-variant duplicates that cannot both be folded: %. '
      'Merge each pair (keep the login with the history, revoke the other) and re-run.',
      collisions
      USING ERRCODE = 'unique_violation';
  END IF;
END $$;

UPDATE auth.users SET email = lower(email) WHERE email <> lower(email);

-- FROM `crm.client_advisers`, which is the only record of who actually holds a book. Gated on the
-- person already being staff so `users_adviser_is_staff` is satisfied by construction rather than
-- by luck: `auth.users.type` is main's 0121 and admits only {} | client | staff | client+staff, so
-- an adviser who is somehow not staff is a row this migration must not create.
UPDATE auth.users u
   SET is_adviser = true
 WHERE 'staff' = ANY(u.type)
   AND NOT u.is_adviser
   AND EXISTS (
     SELECT 1 FROM crm.client_advisers ca
      WHERE ca.adviser_id = u.id AND ca.tenant_id = u.tenant_id
   );

RESET ROLE;

-- The vocabulary narrows to the two that are genuinely peers.
ALTER TABLE auth.users
  ADD CONSTRAINT users_adviser_is_staff
  CHECK (NOT is_adviser OR 'staff' = ANY(type));

-- What makes "we store it folded" true rather than aspirational. See the header.
ALTER TABLE auth.users
  ADD CONSTRAINT users_email_is_folded CHECK (email = lower(email));

-- Redundant now: folded storage plus `users_tenant_email_key` (0001) is the same guarantee. IF
-- EXISTS because no upgrade path creates it any more (see the fold above) — but a database that
-- ran this branch before the reconciliation does have one, and leaving it would keep a second
-- unique index and a second `ON CONFLICT` target for a value that is now folded on the way in.
DROP INDEX IF EXISTS auth.users_tenant_email_ci_key;

COMMENT ON COLUMN auth.users.is_adviser IS
  'Whether this member of staff may hold a client book. A boolean rather than a `type` member '
  'because advising is a capability WITHIN staff, and `{client, adviser}` is not a person — the '
  'users_adviser_is_staff CHECK makes that unrepresentable. No custodian can propose it: SEI''s '
  'DSR_TYPE_NAME has 7 Adviser codes and zero accounts use one, Seccl''s userType is Machine|User, '
  'and Morgan Lloyd names one adviser contact per firm. An operator states it.';
COMMENT ON COLUMN auth.users.email IS
  'Stored FOLDED (lowercase) — users_email_is_folded enforces it, and provisioning.fold_email is '
  'the one implementation every write path routes through. WorkOS normalises this value itself, so '
  'holding mixed case meant holding a spelling the identity provider does not.';
