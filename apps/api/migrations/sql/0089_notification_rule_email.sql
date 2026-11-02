-- 0081_notification_rule_email -- per-rule email copy, authored in the same builder as the in-app
-- notification (the "fold": one authoring surface, two renderings).
--
-- A notification's EMAIL is composed alongside its in-app copy. These two columns hold the email's
-- own subject + body; when either is NULL the email falls back to the in-app title/body (today's
-- behaviour), so existing rules keep sending exactly as before. The branded HTML shell (header /
-- logo / footer) stays the shared template layer — an author writes content, the shell wraps it.

ALTER TABLE notifications.rules
  ADD COLUMN email_subject text,
  ADD COLUMN email_body    text;
