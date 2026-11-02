/*
interlace:
  materialise: view
  description: >
    Daily valuation-push runs, one row per valuation date — the state view over
    the sink.daily_valuations ledger (schemes covered, pushed/failed/dry-run/
    skipped splits, last outcome detail). The row-level ledger itself is
    queryable directly for per-scheme investigation.
*/
select
  valuation_date,
  count(*)                                            as schemes,
  count(*) filter (status = 'pushed')                 as pushed,
  count(*) filter (status = 'failed')                 as failed,
  count(*) filter (status = 'dry-run')                as dry_run,
  count(*) filter (status = 'skipped')                as skipped,
  round(sum(value), 2)                                as total_value,
  max(detail) filter (status = 'failed')              as last_failure
from sink.daily_valuations
group by valuation_date
order by valuation_date desc
