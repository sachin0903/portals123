-- 0135 added a UNIQUE (product_id, role) index before backfilling provider roles. That is not the
-- grain of this table: 0042 explicitly allows one shelf product on several platforms, and both
-- Seccl and SEI can therefore be custodians for the same product. Staging held exactly that valid
-- shape, so 0135's UPDATE collided on (product_id, 'custodian') and rolled the migration back.
--
-- 0135 is corrected in place so a database that has not crossed it can migrate. This defensive
-- drop repairs databases that applied the old text while they happened to contain only one
-- provider per role. It is intentionally unconditional on data and takes no rewrite or long lock.
DROP INDEX IF EXISTS platform.product_platforms_one_provider_per_role;
