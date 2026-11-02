-- 0104_service_type_discretionary — add 'discretionary' to the client service type.
--
-- crm.service_type was ('advised','execution_only'); firms wanted 'Discretionary' selectable on the
-- client Service dropdown. ADD VALUE is safe in a transaction on PG 12+ as long as the new value is
-- not USED in the same transaction (it is not here).
ALTER TYPE crm.service_type ADD VALUE IF NOT EXISTS 'discretionary';
