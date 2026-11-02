-- What may still be subscribed, as the custodian states it, beside the used/limit pair.
--
-- `allowance_limit - allowance_used` is that number only when both exist. Seccl publishes a
-- remaining subscription without a limit until a movement carries `subscriptionAmount`, and
-- storing the remaining figure AS the limit would claim an unused allowance equal to whatever
-- is left. NULL when the custodian has not stated one.
--
-- A (client, wrapper) fact copied onto each account of the wrapper. Readers take max(), never
-- sum(): two ISAs share one allowance.

ALTER TABLE platform.accounts
    ADD COLUMN allowance_remaining numeric(20, 4);

COMMENT ON COLUMN platform.accounts.allowance_remaining IS
    'What may still be subscribed this tax year, as the custodian states it. A (client, wrapper) fact copied onto each account of the wrapper; readers take max(), never sum(). NULL when the custodian has not stated a remaining figure.';
