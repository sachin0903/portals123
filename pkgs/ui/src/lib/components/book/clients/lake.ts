/**
 * Reading a client's provider position out of the lake.
 *
 * These were inline in the platform's `clients/[id]/+page.server.ts` when that route loaded
 * everything the record needed, then moved out as the sections became routes, and now live with
 * the shared book components so BOTH portals' loaders read one definition — the overview needs the
 * classification (the cash leg a withdrawal may be paid from), the client-transactions section
 * needs the movement mapping. Two definitions of "cash" is how they drift apart.
 */

/** Raw lake row (see crm.py list-holdings / list-transactions). */
export type RawHolding = Record<string, unknown>;
export type RawTxn = Record<string, unknown>;

/** A provider figure as a number. A non-finite value counts as nothing rather than poisoning the
 *  total it lands in: one NaN makes a leg NaN, and `amount > NaN` is false — which is the whole
 *  withdrawal gate, defeated by one bad row upstream. Mirrors `instructions.funding_basis`. */
export const asNumber = (x: unknown): number => {
	const n = typeof x === 'number' ? x : Number(x);
	return Number.isFinite(n) ? n : 0;
};

/** Bucket a holding into an asset class from its type/name — the provider's `asset_type` is coarse
 *  ("Stock"), so the donut would otherwise be one flat segment. Mirrors crm.py's `_asset_class`.
 *
 *  The cash test matches on WORD boundaries (as `instructions.is_cash_holding` does): as a bare
 *  substring, `cash` made an equity fund called "Cashel Capital …" part of the balance a withdrawal
 *  may be paid from, so a fund manager's naming choice inflated the withdrawable cash. This is also
 *  the definition the withdrawal gate uses (`_basisByAccount`), so the Cash the screen shows and the
 *  cash a withdrawal is checked against cannot drift apart. */
export function classifyHolding(h: RawHolding): string {
	const s = `${h.asset_type ?? ''} ${h.asset_name ?? ''}`.toLowerCase();
	if (/\b(?:cash|money[ -]?market|liquidity)\b/.test(s)) return 'Cash';
	if (/bond|gilt|treasury|fixed income|credit/.test(s)) return 'Fixed income';
	if (/property|real estate|reit|infrastructure/.test(s)) return 'Property & infra';
	if (/gold|commodit|carbon|precious|metal/.test(s)) return 'Alternatives';
	return 'Equities';
}

/** Map a raw Seccl transaction to the ActivityList icon set (mirrors crm.py). */
export function activityType(t: RawTxn): string {
	const s = `${t.movement_type ?? ''} ${t.type ?? ''} ${t.sub_type ?? ''}`.toLowerCase();
	for (const [needle, kind] of [
		['div', 'dividend'],
		['fee', 'fee'],
		['charge', 'fee'],
		['withdraw', 'withdrawal'],
		['deposit', 'deposit'],
		['sell', 'sell'],
		['redem', 'sell'],
		['buy', 'buy'],
		['subscr', 'buy']
	] as const) {
		if (s.includes(needle)) return kind;
	}
	return 'buy';
}

/** Signed amount for a transaction: money leaving the account reads negative. */
export function signedAmount(t: RawTxn): number {
	const amt = Math.abs(asNumber(t.amount));
	const s = `${t.movement_type ?? ''} ${t.type ?? ''} ${t.sub_type ?? ''}`.toLowerCase();
	const out = ['sell', 'withdraw', 'fee', 'charge', 'out', 'debit'].some((k) => s.includes(k));
	return out ? -amt : amt;
}

/** The shared `ActivityList` row shape. */
export type ActivityRow = {
	date: string;
	type: string;
	description: string;
	amount: number;
	wrapper: string;
	currency?: string | null;
};

/** Lake transactions as ActivityList rows. */
export function toActivity(rows: RawTxn[]): ActivityRow[] {
	return rows.map((t) => ({
		date: String(t.date ?? ''),
		type: activityType(t),
		description: String(t.asset_name ?? t.type ?? 'Transaction'),
		amount: signedAmount(t),
		wrapper: String(t.account_name ?? ''),
		currency: typeof t.currency === 'string' && t.currency ? t.currency : 'GBP'
	}));
}

function roundMoney(n: number): number {
	return Math.round(n * 100) / 100;
}

/**
 * What the client paid for what they hold, and the lake's unrealised gain, scaled the same
 * way the cash figure and allocation ring are — the lake's anonymised absolutes do not
 * reconcile with OLTP AUM, so one factor covers every class.
 *
 * Both return `null` when there is nothing to sum (empty list, or no cost/gain on any row):
 * the header cards render an em dash rather than a zero, because a zero book cost reads as
 * "bought for nothing".
 */
export function clientBookTotals(
	rawHoldings: RawHolding[],
	clientAum: number
): { bookCost: number | null; unrealisedGain: number | null } {
	if (rawHoldings.length === 0) return { bookCost: null, unrealisedGain: null };
	const lakeTotal = rawHoldings.reduce((s, h) => s + asNumber(h.value), 0);
	const scale = lakeTotal > 0 && clientAum > 0 ? clientAum / lakeTotal : 1;
	const hasCost = rawHoldings.some((h) => h.book_cost != null);
	const hasGain = rawHoldings.some((h) => h.growth != null);
	const bookCost = rawHoldings.reduce((s, h) => s + asNumber(h.book_cost), 0) * scale;
	const unrealisedGain = rawHoldings.reduce((s, h) => s + asNumber(h.growth), 0) * scale;
	return {
		bookCost: hasCost ? roundMoney(bookCost) : null,
		unrealisedGain: hasGain ? roundMoney(unrealisedGain) : null
	};
}

/**
 * `GET /crm/holdings/summary?contact_id=` — the first-paint donut, cash figure and book-cost
 * cards. The unpaged holdings list is not a fallback: a missing or failed summary leaves the
 * ring empty rather than pulling every position across the wire.
 *
 * The API is being added in parallel. This is the shape the portals consume; extra keys are
 * ignored, and absent ones degrade to empty / null.
 */
export type HoldingsSummary = {
	allocations?: { label: string; value: number }[];
	by_class?: Record<string, number>;
	cash?: number;
	book_cost?: number | null;
	unrealised_gain?: number | null;
	growth?: number | null;
	lake_total?: number;
	total?: number;
	/** Per-account cash/invested legs, when the summary carries them — the money dialogs' basis. */
	accounts?: {
		account_id: number;
		cash?: number;
		invested?: number;
		as_of?: string | null;
	}[];
};

export type HoldingsSummaryView = {
	allocations: { label: string; value: number }[];
	cash: number;
	bookCost: number | null;
	unrealisedGain: number | null;
	accountLegs: { accountId: number; cash: number; invested: number; asOf: string | null }[];
};

/** Scale a holdings summary onto the client's OLTP AUM, the same factor the unpaged list used. */
export function applyHoldingsSummary(
	summary: HoldingsSummary | null,
	clientAum: number
): HoldingsSummaryView {
	if (!summary) {
		return { allocations: [], cash: 0, bookCost: null, unrealisedGain: null, accountLegs: [] };
	}
	const rawAllocations =
		summary.allocations ??
		Object.entries(summary.by_class ?? {}).map(([label, value]) => ({
			label,
			value: asNumber(value)
		}));
	const lakeTotal =
		summary.lake_total ??
		summary.total ??
		rawAllocations.reduce((sum, row) => sum + asNumber(row.value), 0);
	const scale = lakeTotal > 0 && clientAum > 0 ? clientAum / lakeTotal : 1;
	const allocations = rawAllocations
		.map((row) => ({ label: row.label, value: asNumber(row.value) * scale }))
		.sort((a, b) => b.value - a.value);
	const cashRaw = summary.cash ?? rawAllocations.find((row) => row.label === 'Cash')?.value ?? 0;
	const book = summary.book_cost;
	const gain = summary.unrealised_gain ?? summary.growth ?? null;
	return {
		allocations,
		cash: asNumber(cashRaw) * scale,
		bookCost: book != null ? roundMoney(asNumber(book) * scale) : null,
		unrealisedGain: gain != null ? roundMoney(asNumber(gain) * scale) : null,
		accountLegs: (summary.accounts ?? []).map((row) => ({
			accountId: row.account_id,
			cash: asNumber(row.cash),
			invested: asNumber(row.invested),
			asOf: row.as_of ?? null
		}))
	};
}
