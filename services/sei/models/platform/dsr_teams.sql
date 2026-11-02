/*
interlace:
  materialise: view
  depends_on: [ref.dsr_teams, platform.dsr_grants]
  tags: [sei, entity]
  description: >
    DSR teams with the types they are granted under and how many accounts each
    covers — the admin picker's source and the answer to "which teams are tenants".
*/
-- A team can be granted under more than one type (a firm's advisory relationship is
-- often both "IFA Firm" and "Fee Receiving IFA Firm"), so the types are aggregated
-- rather than assumed single.
with usage as (
  select dsr_id,
         firm_id,
         list_sort(list_distinct(list(dsr_type)))     as dsr_types,
         max(identifies_firm)                         as identifies_firm,
         count(distinct subject_id) filter (where scope = 'account') as account_count
  from platform.dsr_grants
  where grantee = 'team'
  group by dsr_id, firm_id
)
select d.dsr_team_id,
       d.dsr_team_description as dsr_team_name,
       d.firm_id,
       d.firm_name,
       coalesce(u.dsr_types, [])            as dsr_types,
       coalesce(u.identifies_firm, false)   as identifies_firm,
       coalesce(u.account_count, 0)         as account_count,
       d.as_of_date
from ref.dsr_teams d
left join usage u on u.dsr_id = d.dsr_team_id and u.firm_id = d.firm_id
