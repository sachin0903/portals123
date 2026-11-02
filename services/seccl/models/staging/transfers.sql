/*
interlace:
  materialise: virtual
  strategy: merge
  key: transaction_id
  description: Latest transfer-rejection state per transaction
*/
select * exclude (_rn) from (
  select *, row_number() over (partition by transaction_id order by _offset desc) as _rn
  from streams.transfers
) where _rn = 1
