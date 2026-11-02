-- 0082_rule_email_design -- a per-rule RICH email design (the editor's html + css).
--
-- The email fold moves from plain subject/body to a full design authored in the shared email EDITOR
-- (GrapesJS), one per notification rule. `email_html`/`email_css` hold the editor's output; the
-- notification email channel renders them (jinja over the merge context, CSS inlined) instead of the
-- hand-built shell. `email_subject` stays. `email_body` (0081's plain body) is kept as a fallback for
-- a rule that has no design yet — the consumer prefers the design, then the plain body, then the
-- in-app title/body — so nothing sending today regresses.

ALTER TABLE notifications.rules
  ADD COLUMN email_html text,
  ADD COLUMN email_css  text;
