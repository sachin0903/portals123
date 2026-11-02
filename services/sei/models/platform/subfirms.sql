/*
interlace:
  materialise: view
  depends_on: ref.subfirms
  tags: [sei, entity]
  description: Subfirms, each owned by one firm (onshore/offshore and similar splits).
*/
select subfirm_id, subfirm_name, firm_id, firm_name, as_of_date from ref.subfirms
