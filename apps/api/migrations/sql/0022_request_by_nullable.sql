-- ============================================================================
-- 0022_requests_requested_by_nullable.sql
--
-- A flow case created by the client themselves (the public onboarding entry) raises a
-- documents.requests row with NO staff requester — there is no adviser in the loop. The column
-- was NOT NULL (0008, when every request was adviser-raised), so allow NULL now. Staff-raised
-- requests still record the actor (auth.uid() / the flow engine passes the caller's id).
-- ============================================================================
ALTER TABLE documents.requests ALTER COLUMN requested_by DROP NOT NULL;
