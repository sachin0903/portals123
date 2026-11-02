-- Tipping-off (POCA 2002 s.333A) defence-in-depth for the DML trail.
--
-- The aml.* tables (aml.profiles/searches/hits) carry the audit.record trigger, which on every
-- change writes an audit.log row holding the raw before/after (a hit's match name, score, datasets)
-- stamped with the AFFECTED row's tenant_id — for an attributed hit, the FIRM. audit.log's read
-- policy let an `authenticated` firm principal read its own tenant's rows, so the ONLY thing keeping
-- a firm from seeing the raw screening trail was that no firm-facing endpoint issued an
-- entity-unfiltered audit.log query. The action stream already has a structural control here
-- (routers/firm.py FIRM_EXCLUDED_EVENT_PREFIXES + a service_role-only table); give the DML log the
-- same: an `authenticated` principal can NEVER read an aml.* audit row. It does not weaken the
-- adjudicated screening a firm sees via GET /crm/contacts/{id}/screening (that reads the aml.* tables
-- with undecided hits + detail withheld); it blocks only the raw DML trail, which bypasses that
-- withholding. Graphene operations still reads it via GET /admin/audit, which runs as service_role
-- (BYPASSRLS), not through this policy.
--
-- Only the tenant-lineage (firm) branch gains the exclusion; the `tenant_id IS NULL AND is_system()`
-- branch (unattributed rows, ops only) is unchanged.

SET LOCAL lock_timeout = '5s';

ALTER POLICY audit_log_read ON audit.log USING (
    (tenant_id = ANY (auth.tenant_lineage()) AND entity_type NOT LIKE 'aml.%')
    OR (tenant_id IS NULL AND auth.is_system())
);
