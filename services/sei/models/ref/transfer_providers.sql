/*
interlace:
  materialise: view
  depends_on: ref.reference_current
  tags: [sei, ref]
  description: >
    Ceding counterparties for an account transfer. Reference holds them as four
    global code sets (firm_id is null on every row): transfer agents, custodians,
    cash agents and tax-wrap providers. A transfer-in's counterpartyId is the
    code. Brokers stay in ref.codes — that set is the trading book, not a ceding
    scheme.
*/

-- The country suffix is parsed off the name ("Aviva Investors UK Fund Serv Ltd - GB").
-- SEI does not send it as its own column. Names with no trailing country code stay null.
SELECT
    code                                              AS provider_id,
    description                                       AS provider_name,
    CASE type
        WHEN 'TRANSFER_AGENT_ID' THEN 'transfer_agent'
        WHEN 'CUSTODIAN' THEN 'custodian'
        WHEN 'CASH_AGENT' THEN 'cash_agent'
        WHEN 'TAX_WRAP_PROVIDER' THEN 'tax_wrap_provider'
    END                                               AS kind,
    NULLIF(regexp_extract(description, '-\s*([A-Z]{2})\s*$', 1), '')
                                                      AS country_code,
    firm_id,
    as_of_date
FROM ref.reference_current
WHERE type IN (
    'TRANSFER_AGENT_ID',
    'CUSTODIAN',
    'CASH_AGENT',
    'TAX_WRAP_PROVIDER'
)
