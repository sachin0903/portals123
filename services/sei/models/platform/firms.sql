/*
interlace:
  materialise: view
  depends_on: ref.firms
  tags: [sei, entity]
  description: Firms — the top of the tenant key. Mirrors platform.nodes' role as the ownership root.
*/
select firm_id, firm_name, as_of_date from ref.firms
