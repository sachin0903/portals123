-- 0083_rule_email_code -- a notification rule selects an EMAIL TEMPLATE by code.
--
-- Supersedes the per-rule email design (0081 email_subject/email_body, 0082 email_html/email_css):
-- rather than authoring a bespoke design on each rule, a rule points at one of the shared,
-- firm-editable email templates (email.templates, by `code`) — the very templates the Emails page
-- manages, edited once and reused. NULL means the general `notification` wrapper. The two document
-- notifications ship pointing at their own templates (document_sent / request_raised); every other
-- rule can be set the same way from the builder's dropdown.
--
-- The four superseded columns were never read by a send path (the email consumer renders a
-- catalogue `code`, not per-rule html), so dropping them loses nothing that ships today.

ALTER TABLE notifications.rules
  DROP COLUMN IF EXISTS email_subject,
  DROP COLUMN IF EXISTS email_body,
  DROP COLUMN IF EXISTS email_html,
  DROP COLUMN IF EXISTS email_css,
  ADD COLUMN email_code text;
