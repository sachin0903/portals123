/**
 * Display rules for a client's holdings — the order they are read in, and the return printed
 * beside them. Pure, so both are table-testable (holdings.test.ts) rather than asserted through
 * a rendered page.
 */
import type { Holding as PortalHolding } from '@graphene/ui';
import type { Holding } from '$lib/api';

/**
 * Is this row the account's CASH, as opposed to an investment?
 *
 * DELIBERATELY NARROWER than `is_cash_holding` in apps/api (`app/instructions.py`), and the two
 * must not be merged. That one answers "what can a withdrawal be paid FROM", where a money-market
 * fund is near enough to cash to spend and counting it is the safe direction. This answers "which
 * row is the uninvested balance", and there a money-market fund is an investment the client chose,
 * with an ISIN, a unit price and a return.
 *
 * Reading the wider rule here put "Royal London Short Term Money Market Y Acc" and its Inc twin
 * above the alphabetical list, which is simply a holdings table that does not look sorted.
 *
 * So: the provider says so (`asset_type` is Cash, or SEI's currency code — it names a currency
 * holding by the currency, so nothing about it contains the word), or the row carries no
 * instrument at all and is called cash. A fund is never caught by any of the three.
 */
const CASH_TYPE_CODES = new Set(['CASH', 'MRCXXXXXXX']);

export function isCashHolding(h: Pick<Holding, 'asset_type' | 'asset_name' | 'isin'>): boolean {
	if (CASH_TYPE_CODES.has((h.asset_type ?? '').trim().toUpperCase())) return true;
	// No instrument AND named cash — not one or the other: an unmatched equity can arrive with a
	// blank ISIN, and "Cash Fund" is a fund.
	return !h.isin?.trim() && (h.asset_name ?? '').trim().toLowerCase() === 'cash';
}

/** A-Z the way the shared DataTable sorts — `en-GB`, numeric, so "Fund 2" precedes "Fund 10". */
const byName = new Intl.Collator('en-GB', { numeric: true, sensitivity: 'base' });

/**
 * The order an account's holdings are read in: CASH FIRST, then everything else A-Z.
 *
 * Not the order they arrive in, which is the lake's `market_value DESC`. Value order answers
 * "where is the money", which is the question the portal's own sortable holdings TABLE is for
 * (and it keeps that default). This list has no sort controls at all, so its order is the only
 * one a client gets — and on a record they return to, the useful property is being able to find
 * a named holding again, which alphabetical gives and value order does not.
 *
 * Cash is lifted out because it is not one of the investments: it is what has not been invested,
 * and burying it at whatever rank its balance happens to earn makes "how much is uninvested" a
 * question you answer by scanning.
 *
 * A holding with no name sorts LAST rather than first — the collator would otherwise put every
 * blank at the top, which is the shared DataTable's rule too (blanks last in both directions).
 */
export function orderHoldings<T extends Pick<Holding, 'asset_type' | 'asset_name' | 'isin'>>(
	holdings: readonly T[]
): T[] {
	return [...holdings].sort((a, b) => {
		const cash = Number(isCashHolding(b)) - Number(isCashHolding(a));
		if (cash !== 0) return cash;
		const an = (a.asset_name ?? '').trim();
		const bn = (b.asset_name ?? '').trim();
		if (!an !== !bn) return an ? -1 : 1;
		return byName.compare(an, bn);
	});
}

/**
 * A holding's return, as a FRACTION (0.136 = +13.6%), or null when there is no honest answer.
 *
 * Derived from growth over book cost, and NOT read from `growth_pct`. That column is the lake's
 * `unrealised_gain_percent`, which is a PERCENT — the API's own `_holding_return` says so, and
 * says what happens when it is fed to a formatter expecting a fraction: "+6,303%". The account
 * record trusted the type's declared contract instead (which said fraction, and was wrong), ran
 * every value through `sanePct`'s ±1000% sanity bound, got null for all of them, and printed a
 * dash in the Return column of every holding a client owns.
 *
 * Null rather than zero for the two cases where nothing can be computed: no book cost to divide
 * by, and a result so far out that the basis must be wrong. Zero is a claim — "this holding has
 * neither gained nor lost" — and the reader cannot tell it from the truth, which is that we do
 * not know. A caller whose shape has no room for "unknown" coalesces it deliberately.
 */
export function holdingReturn(h: Pick<Holding, 'book_cost' | 'growth'>): number | null {
	const cost = h.book_cost ?? 0;
	if (cost <= 0) return null;
	const pct = (h.growth ?? 0) / cost;
	return Math.abs(pct) > 10 ? null : Math.round(pct * 1e4) / 1e4;
}

/**
 * Project one raw /crm/holdings row into the shape the holdings block renders — the same field
 * mapping /crm/home applies server-side to its top-5 slice: asset_name/isin/account
 * name/quantity/price/value.
 *
 * Lives HERE, not in `page-data.ts`, because the account record's `+page.svelte` renders the
 * shared `HoldingsTable` and so needs this projection in the BROWSER. `page-data.ts` imports
 * `$lib/api` for its values, and `$lib/api` reads `$env/dynamic/private`, which makes the whole
 * module server-only — importing it into a component is a build error, not a runtime one. This
 * file imports `$lib/api` for its types alone.
 */
export function toPortalHolding(h: Holding): PortalHolding {
	// Cash is a balance, not a fund. "Pound Sterling, 693.644 units" is the custodian naming
	// the currency and counting pounds as units at a price of 1. The row is the amount.
	const cash = isCashHolding(h);
	return {
		name: cash ? 'Cash' : (h.asset_name ?? '—'),
		ticker: cash ? undefined : (h.isin ?? undefined),
		wrapper: h.account_name ?? '',
		units: cash ? 0 : (h.quantity ?? 0),
		price: cash ? 0 : (h.price ?? 0),
		value: h.value ?? 0,
		// Null survives to the renderer, which prints "—". It used to coalesce to 0, so the
		// /holdings page claimed "+0.0%" on every row whose cost the custodian never sent —
		// more than half a real book — while the account record admitted it did not know.
		// Cash has no return either: book cost equals the balance, which would print "+0.0%".
		gainPct: cash ? null : holdingReturn(h),
		assetClass: h.asset_class ?? null,
		...(cash ? { cash: true } : {}),
		lookthroughHref:
			cash || !h.lookthrough_uid
				? undefined
				: `/look-through/${h.lookthrough_uid}?${new URLSearchParams({
						...(h.lookthrough_as_of ? { as_of: h.lookthrough_as_of } : {}),
						...(h.account_uid ? { account: h.account_uid } : {})
					}).toString()}`
	};
}
