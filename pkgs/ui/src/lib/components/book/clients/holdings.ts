/**
 * An optional later fill for cash and growth. The listings now carry the stored valuation on
 * the row, so nothing asks for one. Zero is a measured flat position; a give-up leaves the
 * row's own figure alone.
 */
export type ContactHolding = { id: number; cash: number; growth: number };

/** A finished cash/growth fill, keyed by client id. */
export type ContactHoldings = {
	metrics: ContactHolding[];
	timed_out: boolean;
};

/** `?ids=1&ids=2` — repeated, the shape FastAPI binds to `list[int]`. */
export function contactHoldingsQuery(ids: readonly number[]): string {
	const params = new URLSearchParams();
	for (const id of ids) params.append('ids', String(id));
	return params.toString();
}

/** Ids whose cash has not been measured. A number, including zero, means the list already waited
 * (a cash/growth sort, or an export) and a second read would only repeat it. */
export function unmeasuredIds(rows: readonly { id: number; cash: number | null }[]): number[] {
	return rows.filter((row) => row.cash == null).map((row) => row.id);
}

type HoldingRow = { id: number; cash: number | null; growth: number | null };

/** Paint a finished read onto the rows it belongs to. A timeout, a failure, or an empty
 * answer leaves the rows alone, so a dash stays a dash and a zero is never invented. */
export function applyHoldings<T extends HoldingRow>(
	rows: readonly T[],
	fill: ContactHoldings | null | undefined
): T[] {
	if (!fill || fill.timed_out || fill.metrics.length === 0) return rows as T[];
	const byId = new Map(fill.metrics.map((metric) => [metric.id, metric]));
	return rows.map((row) => {
		const found = byId.get(row.id);
		return found ? { ...row, cash: found.cash, growth: found.growth } : row;
	});
}
