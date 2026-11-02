/*
interlace:
  materialise: virtual
  strategy: merge
  key: id
  description: Latest webhook-delivered state per position
*/
select * exclude (_rn) from (
  select *, row_number() over (partition by id order by _offset desc) as _rn
  from streams.positions
) where _rn = 1
