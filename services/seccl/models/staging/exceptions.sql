/*
interlace:
  materialise: virtual
  strategy: merge
  key: id
  description: Latest state per exception notification delivered by webhook
*/
select * exclude (_rn) from (
  select *, row_number() over (partition by id order by _offset desc) as _rn
  from streams.exceptions
) where _rn = 1
