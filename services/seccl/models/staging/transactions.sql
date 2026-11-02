/*
interlace:
  materialise: virtual
  strategy: merge
  key: id
  description: Latest webhook-delivered state per transaction (re-runs on every stream flush)
*/
select * exclude (_rn) from (
  select *, row_number() over (partition by id order by _offset desc) as _rn
  from streams.transactions
) where _rn = 1
