/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.third_party_products, platform.nodes]
  tags: [seccl, entity, conformed]
  description: >
    Third-party products a firm can hold alongside its platform wrappers — offshore
    bonds, external pension schemes — conformed from Seccl's third-party product
    register.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: name
*/

-- FIRM DATA, unlike transfer_providers. Verified against staging: these carry the client
-- firm's id ('SELCT') and an `available_to_node_id` list, so they are scoped by org -- a firm
-- must not see another firm's offshore bond arrangements.
--
-- available_to_node_id is a LIST ('["0"]' on staging). Taking the first element matches how
-- every other Seccl model resolves the scalar-or-list node quirk; node '0' is the firm root.
--
-- A ROW AVAILABLE AT THE FIRM ROOT IS FIRM-WIDE, and publishing the root's own lineage for it
-- withheld every one of these from every tenant. `lineage` containment
-- (fanout._conformed_predicate) tests ANCESTRY: a row owned by a sub-node publishes that node's
-- root->leaf path, so a tenant holding any ancestor matches -- which is correct and is why the
-- ELSE branch below is unchanged. But the root's own path is just [root], and a tenant enabled on
-- a DESCENDANT holds a key that path does not contain. Verified on dev: all 7 of this firm's
-- third-party products carry `["0"]`, the tenant is enabled on node 1, and the fan-out projected
-- 0 of 7 -- which read as "this firm has no third-party products" rather than as a scoping bug.
--
-- So a root-available row publishes the empty lineage that admits it to every tenant, the same
-- treatment instruments, transfer_providers and fee_packages get, and on the same premise: one
-- Seccl firm per catalogue (interlace.yaml gives staging and production separate metadata schemas
-- and control planes). A row naming a specific sub-node still narrows to that subtree.
WITH source AS (
    SELECT
        t.*,
        coalesce(
            try_cast(cast(t.available_to_node_id AS varchar) AS varchar[])[1],
            cast(t.available_to_node_id AS varchar)
        ) AS node_key
    FROM raw.third_party_products AS t
)
SELECT
    cast(src.id AS varchar)                                   AS external_id,
    src.node_key                                              AS key,
    -- `[]` HERE IS DELIBERATE and is the one case where it is: the register is FIRM-WIDE, and
    -- containment tests ANCESTRY, so a row carrying the firm ROOT's path matches only a tenant
    -- holding the root and every tenant on a sub-node gets none of its own firm's providers —
    -- which is exactly how this relation came to project 0 of 7 rows. A row scoped to a
    -- sub-node still narrows on that node's own ancestry.
    --
    -- The ELSE arm no longer coalesces to `[]`: that spelt "this node is unresolvable" as
    -- "global", so one bad node id published a provider to every tenant on the platform. NULL
    -- is what "no org" spells (`book_sink.predicate.conformed` tests it to false).
    CASE
        WHEN o.parent_key IS NULL THEN []::varchar[]
        ELSE o.lineage
    END                                                       AS lineage,
    coalesce(src.product_name, src.scheme_name)                AS name,
    src.scheme_name,
    -- Seccl's wrapper vocabulary, same as accounts: TPOFFBOND, TPPENSION, TPONBOND. The
    -- materialiser maps these onto platform.product_type, so a third-party provider can be
    -- attached to the product it actually provides.
    src.wrapper_type                                          AS product_key,
    src.contact_name,
    src.contact_number,
    src.tax_wrapper_administrator,
    src.pension_scheme_tax_reference,
    src.scheme_fca_reference_number,
    src.relief_at_source_reference_number,
    src.origo_identifiers,
    src.external_system,
    try_cast(src.audit_details ->> 'updateDate' AS timestamp)  AS source_updated_at,
    coalesce(try_cast(src.audit_details ->> 'updateDate' AS date), current_date) AS as_of_date,
    to_json(src)                                              AS data
FROM source AS src
LEFT JOIN platform.nodes AS o ON o.key = src.node_key
WHERE coalesce(src.product_name, src.scheme_name) IS NOT NULL
