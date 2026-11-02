/*
interlace:
  materialise: view
  depends_on: [ref.reference_current, ref.firms]
  tags: [sei, ref, fanout]
  description: >
    SEI DSR teams (Data Segregation Rule teams) per firm — the finest level of the
    tenant fan-out key. The admin dsr_ids multi-select reads this, filtered by firm.
*/

-- A DSR team is how SEI segregates a firm's book between servicing teams / IFA
-- firms; `dsr_type_names` gives the team's category (IFA Firm, Client Service
-- Team, Service Type…). Firm '1' is SEI's own internal operations teams — never a
-- tenant grant, so it is excluded here rather than left to leak into a dropdown.
SELECT
    d.code            AS dsr_team_id,
    d.description     AS dsr_team_description,
    d.firm_id,
    f.firm_name,
    d.as_of_date
FROM ref.reference_current d
LEFT JOIN ref.firms f ON f.firm_id = d.firm_id
WHERE d.type = 'DSR'
  AND d.firm_id <> '1'
