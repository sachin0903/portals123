-- 0096_event_vocabulary: rewrite historic event types to the declared `resource.verb` vocabulary.
--
-- WHAT CHANGED, AND WHY THE ROWS MOVE WITH IT. An event type used to be `action.` + the route's
-- OpenAPI operation id, i.e. `{router tag}-{handler name}` — a description of the code's shape
-- rather than of the domain, which is why the stream carried names like
-- `action.admin-admin-create-email-template` and gave the SAME ACT two names when it had two doors
-- (ops and the firm). It is now a declared name per act: `document.sent`, `client.created`,
-- `email_template.version_published` (`app/actions.py::EVENT_TYPES`).
--
-- IS REWRITING AN APPEND-ONLY TABLE ACCEPTABLE? This one time, yes, and the distinction matters:
-- `event_type` is a LABEL THE PLATFORM ASSIGNS to an operation, not a fact a user supplied and not
-- an observation about what happened. Every fact on the row — actor, target firm, route, method,
-- status, path params, captured subject, timestamp — is untouched, and `route` still records the
-- literal endpoint, so the old operation remains identifiable from the row itself. What moves is our
-- own word for it. Leaving both spellings in the table instead would mean every reader, filter,
-- notification rule and automation trigger had to know two names for one act for ever, which is the
-- exact defect the rename set out to remove.
--
-- The mapping is one-way, and several old names collapse onto one new one because the ops door and
-- the firm door were always the same act. It is NOT total over history — it is built from the CURRENT
-- route table, so an event from a RETIRED route has no entry; see the report at the end of this file.
--
-- IRREVERSIBLE ON PURPOSE. `downgrade` does not restore the old strings: the collapse is
-- many-to-one, so there is no unique inverse — reversing `email_template.created` cannot know which
-- of the two doors a given row came through. `route` does (it names the path), so a genuine rollback
-- would derive from that rather than from a guess this migration could bake in wrongly.

CREATE TEMP TABLE event_vocabulary_rename (old text PRIMARY KEY, new text NOT NULL);

INSERT INTO event_vocabulary_rename (old, new) VALUES
    ('action.admin-admin-archive-email-template', 'email_template.archived'),
    ('action.admin-admin-create-email-template', 'email_template.created'),
    ('action.admin-admin-email-templates-load-defaults', 'email_template.defaults_loaded'),
    ('action.admin-admin-email-templates-provision', 'email_template.preset_provisioned'),
    ('action.admin-admin-preview-email-template', 'email_template.previewed'),
    ('action.admin-admin-publish-email-template-version', 'email_template.version_published'),
    ('action.admin-admin-restore-email-template-version', 'email_template.version_restored'),
    ('action.admin-admin-save-email-template-version', 'email_template.version_saved'),
    ('action.admin-admin-send-test-email-template', 'email_template.test_sent'),
    ('action.admin-admin-unarchive-email-template', 'email_template.unarchived'),
    ('action.admin-admin-update-email-template', 'email_template.updated'),
    ('action.admin-create-notification-rule', 'notification_rule.created'),
    ('action.admin-create-tenant', 'firm.created'),
    ('action.admin-create-tenant-member', 'member.created'),
    ('action.admin-create-tenant-role', 'role.created'),
    ('action.admin-deactivate-tenant-member', 'member.deactivated'),
    ('action.admin-delete-notification-rule', 'notification_rule.deleted'),
    ('action.admin-delete-tenant', 'firm.deleted'),
    ('action.admin-grant-member-portal', 'member.portal_granted'),
    ('action.admin-load-notification-defaults', 'notification_rule.defaults_loaded'),
    ('action.admin-platform-query', 'platform.queried'),
    ('action.admin-platform-rebuild', 'platform.rebuilt'),
    ('action.admin-platform-sync', 'platform.sync_started'),
    ('action.admin-provision-all-tenant-storage', 'firm.storage_provisioned_for_all'),
    ('action.admin-provision-tenant-storage', 'firm.storage_provisioned'),
    ('action.admin-reactivate-tenant-member', 'member.reactivated'),
    ('action.admin-refresh-environment-registry', 'environment.registry_refreshed'),
    ('action.admin-refresh-platform-tenants', 'platform.all_firms_refreshed'),
    ('action.admin-refresh-tenant-provider', 'firm.provider_data_refreshed'),
    ('action.admin-refresh-tenant-usage', 'firm.usage_refreshed'),
    ('action.admin-register-environment', 'environment.registered'),
    ('action.admin-resend-tenant-member-invite', 'member.invite_resent'),
    ('action.admin-reset-tenant-member-mfa', 'member.mfa_reset'),
    ('action.admin-reveal-tenant-member-invitation', 'member.invitation_revealed'),
    ('action.admin-revoke-member-portal', 'member.portal_revoked'),
    ('action.admin-run-platform-action', 'platform.action_ran'),
    ('action.admin-set-automation-platform-pause', 'automation.platform_pause_updated'),
    ('action.admin-set-branding', 'branding.updated'),
    ('action.admin-set-environment-secrets', 'environment.secrets_updated'),
    ('action.admin-set-member-portal-role', 'member.portal_role_updated'),
    ('action.admin-set-notification-config', 'notification.settings_updated'),
    ('action.admin-set-provider-source', 'platform.provider_source_updated'),
    ('action.admin-set-tenant-ai', 'firm.ai_settings_updated'),
    ('action.admin-set-tenant-provider', 'firm.provider_updated'),
    ('action.admin-set-tenant-role-permissions', 'role.permissions_updated'),
    ('action.admin-sync-tenant', 'firm.synced'),
    ('action.admin-sync-tenant-roles', 'firm.roles_synced'),
    ('action.admin-update-notification-rule', 'notification_rule.updated'),
    ('action.admin-update-tenant', 'firm.updated'),
    ('action.admin-upload-branding-asset', 'branding.asset_uploaded'),
    ('action.aml-connect-aml-operator', 'aml.operator_connected'),
    ('action.aml-decide-aml-hit', 'aml.hit_decided'),
    ('action.aml-disconnect-aml-operator', 'aml.operator_disconnected'),
    ('action.aml-link-aml-profile', 'aml.profile_linked'),
    ('action.aml-run-aml-screen', 'aml.screen_ran'),
    ('action.aml-run-aml-sync', 'aml.sync_ran'),
    ('action.aml-set-aml-monitoring', 'aml.monitoring_updated'),
    ('action.applications-acknowledge-application-terms', 'journey.terms_accepted'),
    ('action.applications-action-application-step', 'journey.step_actioned'),
    ('action.applications-cancel-application', 'journey.cancelled'),
    ('action.applications-create-application', 'journey.created'),
    ('action.applications-retry-application-step', 'journey.step_retried'),
    ('action.applications-start-application', 'journey.started'),
    ('action.applications-submit-application-step-form', 'journey.step_form_submitted'),
    ('action.auth-activated', 'authentication.account_activated'),
    ('action.auth-login', 'authentication.succeeded'),
    ('action.auth-login-failed', 'authentication.failed'),
    ('action.auth-logout', 'authentication.signed_out'),
    ('action.auth-mfa-enrolled', 'authentication.mfa_enrolled'),
    ('action.auth-mfa-removed', 'authentication.mfa_removed'),
    ('action.automations-create-automation', 'automation.created'),
    ('action.automations-delete-automation', 'automation.deleted'),
    ('action.automations-disable-automation', 'automation.disabled'),
    ('action.automations-enable-automation', 'automation.enabled'),
    ('action.automations-reorder-automations', 'automation.reordered'),
    ('action.automations-set-automation-settings', 'automation.settings_updated'),
    ('action.automations-test-automation', 'automation.tested'),
    ('action.automations-update-automation', 'automation.updated'),
    ('action.catalogue-create-commentary', 'model.commentary_created'),
    ('action.catalogue-create-fee-package', 'fee_package.created'),
    ('action.catalogue-create-model', 'model.created'),
    ('action.catalogue-create-model-version', 'model.version_created'),
    ('action.catalogue-create-product', 'product.created'),
    ('action.catalogue-create-provider', 'provider.created'),
    ('action.catalogue-publish-model-version', 'model.version_published'),
    ('action.catalogue-set-product-platform', 'product.platform_reference_updated'),
    ('action.catalogue-set-term', 'terminology.updated'),
    ('action.catalogue-update-commentary', 'model.commentary_updated'),
    ('action.catalogue-update-fee-package', 'fee_package.updated'),
    ('action.catalogue-update-model', 'model.updated'),
    ('action.catalogue-update-product', 'product.updated'),
    ('action.catalogue-update-provider', 'provider.updated'),
    ('action.contributions-attach-contribution-transaction', 'contribution.transaction_attached'),
    ('action.contributions-cancel-contribution', 'contribution.cancelled'),
    ('action.contributions-categorise-contribution', 'contribution.categorised'),
    ('action.contributions-classify-transaction', 'contribution.transaction_classified'),
    ('action.contributions-create-contribution', 'contribution.created'),
    ('action.contributions-detach-contribution-transaction', 'contribution.transaction_detached'),
    ('action.contributions-lodge-contribution-expectation', 'contribution.expectation_lodged'),
    ('action.contributions-record-contribution-to-morgan-lloyd', 'contribution.recorded_at_morgan_lloyd'),
    ('action.contributions-refresh-contribution-from-morgan-lloyd', 'contribution.refreshed_from_morgan_lloyd'),
    ('action.contributions-run-contribution-matcher', 'contribution.matcher_ran'),
    ('action.contributions-set-contribution-status', 'contribution.status_updated'),
    ('action.contributions-stop-contribution-at-morgan-lloyd', 'contribution.stopped_at_morgan_lloyd'),
    ('action.contributions-update-contribution-application', 'contribution.application_updated'),
    ('action.crm-account-drawdown-session', 'account.drawdown_session_started'),
    ('action.crm-create-illustration', 'illustration.created'),
    ('action.crm-create-investment-illustration', 'illustration.investment_created'),
    ('action.crm-push-contact-to-morgan-lloyd', 'client.pushed_to_morgan_lloyd'),
    ('action.crm-set-account-model', 'account.model_updated'),
    ('action.crm-set-contact-bank-account', 'client.bank_account_updated'),
    ('action.crm-set-contact-vulnerability', 'client.vulnerability_updated'),
    ('action.crm-sync-contact-to-morgan-lloyd', 'client.synced_to_morgan_lloyd'),
    ('action.datasets-add-dataset-version', 'dataset.version_added'),
    ('action.datasets-create-dataset', 'dataset.created'),
    ('action.datasets-delete-dataset', 'dataset.deleted'),
    ('action.datasets-update-dataset', 'dataset.updated'),
    ('action.documents-archive-document', 'document.archived'),
    ('action.documents-cancel-document-request', 'document_request.cancelled'),
    ('action.documents-classify-document-type', 'document.type_classified'),
    ('action.documents-create-document-request', 'document_request.created'),
    ('action.documents-create-document-type', 'document_type.created'),
    ('action.documents-fulfill-request-item', 'document_request.item_fulfilled'),
    ('action.documents-generate-document', 'document.generated'),
    ('action.documents-reassign-request-item', 'document_request.item_reassigned'),
    ('action.documents-render-document', 'document.rendered'),
    ('action.documents-retry-document', 'document.render_retried'),
    ('action.documents-send-document', 'document.sent'),
    ('action.documents-submit-request-form-response', 'document_request.form_submitted'),
    ('action.documents-unarchive-document', 'document.unarchived'),
    ('action.documents-update-document', 'document.updated'),
    ('action.documents-update-document-request', 'document_request.updated'),
    ('action.documents-update-document-type', 'document_type.updated'),
    ('action.documents-upload-document', 'document.uploaded'),
    ('action.documents-view-document', 'document.viewed'),
    ('action.email-templates-archive-email-template', 'email_template.archived'),
    ('action.email-templates-create-email-template', 'email_template.created'),
    ('action.email-templates-preview-email-template', 'email_template.previewed'),
    ('action.email-templates-publish-email-template-version', 'email_template.version_published'),
    ('action.email-templates-restore-email-template-version', 'email_template.version_restored'),
    ('action.email-templates-save-email-template-version', 'email_template.version_saved'),
    ('action.email-templates-send-test-email-template', 'email_template.test_sent'),
    ('action.email-templates-unarchive-email-template', 'email_template.unarchived'),
    ('action.email-templates-update-email-template', 'email_template.updated'),
    ('action.files-delete-file', 'file.deleted'),
    ('action.files-upload-file', 'file.uploaded'),
    ('action.firm-add-contact-note', 'client.note_added'),
    ('action.firm-archive-contact', 'client.archived'),
    ('action.firm-assign-adviser', 'client.adviser_assigned'),
    ('action.firm-cancel-instruction', 'instruction.cancelled'),
    ('action.firm-create-contact', 'client.created'),
    ('action.firm-create-instruction', 'instruction.created'),
    ('action.firm-create-role', 'role.created'),
    ('action.firm-deactivate-member', 'member.deactivated'),
    ('action.firm-firm-create-notification-rule', 'notification_rule.created'),
    ('action.firm-firm-delete-notification-rule', 'notification_rule.deleted'),
    ('action.firm-firm-update-notification-rule', 'notification_rule.updated'),
    ('action.firm-grant-portal', 'member.portal_granted'),
    ('action.firm-invite-contact-to-portal', 'client.portal_invited'),
    ('action.firm-invite-member', 'member.invited'),
    ('action.firm-link-contact-user', 'client.portal_user_linked'),
    ('action.firm-reactivate-member', 'member.reactivated'),
    ('action.firm-resend-invite', 'member.invite_resent'),
    ('action.firm-restore-contact', 'client.restored'),
    ('action.firm-revoke-portal', 'member.portal_revoked'),
    ('action.firm-set-branding', 'branding.updated'),
    ('action.firm-set-portal-role', 'member.portal_role_updated'),
    ('action.firm-set-role-permissions', 'role.permissions_updated'),
    ('action.firm-unassign-adviser', 'client.adviser_unassigned'),
    ('action.firm-unlink-contact-user', 'client.portal_user_unlinked'),
    ('action.firm-update-contact', 'client.updated'),
    ('action.firm-upload-branding-asset', 'branding.asset_uploaded'),
    ('action.flows-create-flow', 'flow.created'),
    ('action.flows-publish-flow-version', 'flow.version_published'),
    ('action.flows-replay-dead-letter', 'flow.dead_letter_replayed'),
    ('action.flows-save-flow-version', 'flow.version_saved'),
    ('action.flows-simulate-flow', 'flow.simulated'),
    ('action.flows-update-flow', 'flow.updated'),
    ('action.flows-validate-flow-definition', 'flow.validated'),
    ('action.forms-create-form', 'form.created'),
    ('action.forms-create-form-response', 'form.response_submitted'),
    ('action.forms-delete-form', 'form.deleted'),
    ('action.forms-publish-form', 'form.published'),
    ('action.forms-set-form-fields', 'form.fields_updated'),
    ('action.forms-unarchive-form', 'form.unarchived'),
    ('action.forms-update-form', 'form.updated'),
    ('action.messaging-messaging-conversation-read', 'conversation.read'),
    ('action.messaging-messaging-conversation-send', 'message.sent'),
    ('action.messaging-messaging-conversation-send-attachment', 'message.attachment_sent'),
    ('action.messaging-messaging-conversations-open', 'conversation.opened'),
    ('action.messaging-messaging-group-create-request', 'message_group.document_requested'),
    ('action.messaging-messaging-group-send', 'message_group.message_sent'),
    ('action.messaging-messaging-group-send-attachment', 'message_group.attachment_sent'),
    ('action.messaging-messaging-groups-create', 'message_group.created'),
    ('action.messaging-messaging-thread-read', 'conversation.read'),
    ('action.messaging-messaging-thread-send', 'message.sent'),
    ('action.messaging-messaging-thread-send-attachment', 'message.attachment_sent'),
    ('action.messaging-messaging-ws-ticket', 'message.stream_opened'),
    ('action.notifications-dismiss-notification', 'notification.dismissed'),
    ('action.notifications-mark-all-notifications-read', 'notification.all_read'),
    ('action.notifications-mark-notification-read', 'notification.read'),
    ('action.notifications-set-notification-client-preferences', 'notification.client_preferences_updated'),
    ('action.notifications-set-notification-preferences', 'notification.preferences_updated'),
    ('action.notifications-ws-ticket', 'notification.stream_opened'),
    ('action.shares-access-document-share', 'document_share.accessed'),
    ('action.shares-create-document-share', 'document_share.created'),
    ('action.shares-revoke-share', 'document_share.revoked'),
    ('action.storage-issue-member-storage-credentials', 'storage_credential.issued_for_member'),
    ('action.storage-issue-own-storage-credentials', 'storage_credential.issued_own'),
    ('action.storage-revoke-storage-credentials', 'storage_credential.revoked'),
    ('action.tasks-complete-task', 'task.completed'),
    ('action.tasks-create-task', 'task.created'),
    ('action.tasks-update-task', 'task.updated'),
    ('action.templates-create-template', 'template.created'),
    ('action.templates-delete-template', 'template.deleted'),
    ('action.templates-duplicate-template', 'template.duplicated'),
    ('action.templates-publish-template-version', 'template.version_published'),
    ('action.templates-render-template-preview', 'template.preview_rendered'),
    ('action.templates-render-template-preview-pdf', 'template.preview_pdf_rendered'),
    ('action.templates-save-template-version', 'template.version_saved'),
    ('action.templates-unarchive-template', 'template.unarchived'),
    ('action.templates-update-template', 'template.updated'),
    ('action.transfers-attach-transfer-transaction', 'transfer.transaction_attached'),
    ('action.transfers-create-transfer', 'transfer.created'),
    ('action.transfers-detach-transfer-transaction', 'transfer.transaction_detached'),
    ('action.transfers-set-transfer-status', 'transfer.status_updated');

-- One statement, so a partially-renamed stream is not a state that can be observed.
UPDATE audit.actions a
   SET event_type = r.new
  FROM event_vocabulary_rename r
 WHERE a.event_type = r.old;

-- The SAME rename in `notifications.notifications`, which stores the event type it was raised from
-- (for humanising and filtering the bell's feed). It is the same label for the same operation, so it
-- moves with it — otherwise a firm's notification centre keeps describing acts in a vocabulary the
-- rest of the product has stopped using, and a filter written against either spelling misses half.
UPDATE notifications.notifications n
   SET event_type = r.new
  FROM event_vocabulary_rename r
 WHERE n.event_type = r.old;

-- WHAT IS LEFT IS HISTORY WE CANNOT RENAME, AND THAT MUST NOT FAIL THE DEPLOY.
--
-- The mapping is built from the CURRENT route table, so it covers every event a running instance can
-- emit. It cannot cover an event from a route that has since been RETIRED — `action.tags-apply-tag`
-- and `action.tags-remove-tag` are two that exist in real streams and have no router any more. There
-- is no name to give them: a `resource.verb` name asserts which resource and verb an operation had,
-- and inventing one for an operation nobody can look up would write fiction into an evidence trail.
--
-- So a leftover is REPORTED, not raised. This check began as `RAISE EXCEPTION`, which is the wrong
-- failure mode twice over: it turns "this deployment has old history" into a failed migration, and it
-- would have been discovered on the deployment rather than here.
--
-- The rows keep working. Every reader that must not lose history already accepts the legacy shape —
-- the AML deny list (`aml.` AND `action.aml-`, so a retired screening op stays denied to firms by its
-- PREFIX whether or not it was ever mapped), the feed's hidden types, the document-family predicate,
-- the `{uid}` resolution, and the UI's humanising (which falls back to stripping the router tag). A
-- retired name shows in the event-type picker in its old shape, which is honest: that operation does
-- not exist any more.
--
-- The thing this check was really for — a LIVE route with no declared name — is owned by
-- `tests/test_actions_tripwire.py::test_every_mutating_route_has_a_declared_event_type`, which fails
-- in CI where it can be fixed, instead of mid-deploy where it cannot.
DO $$
DECLARE
  retired text;
  retired_rows bigint;
BEGIN
  SELECT string_agg(DISTINCT event_type, ', '), sum(n)
    INTO retired, retired_rows
    FROM (
      SELECT event_type, count(*) AS n FROM audit.actions
       WHERE event_type LIKE 'action.%' GROUP BY event_type
      UNION ALL
      SELECT event_type, count(*) FROM notifications.notifications
       WHERE event_type LIKE 'action.%' GROUP BY event_type
    ) s;
  IF retired IS NOT NULL THEN
    RAISE NOTICE
      'event vocabulary: % row(s) keep a pre-rename name, from route(s) that no longer exist: %. '
      'These stay readable (the legacy spelling is accepted by the humanising, the AML deny list, '
      'the hidden-type list and the uid resolution) and are deliberately not renamed.',
      retired_rows, retired;
  END IF;
END $$;

DROP TABLE event_vocabulary_rename;
