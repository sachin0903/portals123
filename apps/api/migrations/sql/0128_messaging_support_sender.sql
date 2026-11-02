SET LOCAL lock_timeout = '5s';

ALTER TABLE messaging.messages
    ADD COLUMN sender_is_support boolean NOT NULL DEFAULT false,
    ADD COLUMN sender_display_name text;

COMMENT ON COLUMN messaging.messages.sender_is_support IS
    'True when a Graphene operations principal sent the message into a target firm workspace.';

COMMENT ON COLUMN messaging.messages.sender_display_name IS
    'Sender name captured at send time so a target-firm client can see a cross-tenant support author under RLS.';
