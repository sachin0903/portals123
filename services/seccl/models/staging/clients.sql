/*
interlace:
  materialise: virtual
  strategy: merge
  key: client_id
  description: Latest webhook-delivered state per client
*/
select * exclude (_rn) from (
  select *, row_number() over (partition by client_id order by _offset desc) as _rn
  from streams.clients
) where _rn = 1
