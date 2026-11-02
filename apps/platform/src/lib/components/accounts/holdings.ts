import { providerReference, type Holding } from '@graphene/ui';

/** Raw lake holding shape (see crm.py list-holdings). */
export type RawHolding = Record<string, unknown>;

const num = (x: unknown): number => (typeof x === 'number' ? x : Number(x) || 0);

type AccountRef = {
	id: number;
	uid: string;
	name: string | null;
	currency: string | null;
};

/**
 * Map a lake holdings row to the book `Holding` the record renders.
 *
 * The lake's `growth_pct` is a percent that reads as +6,303% once formatted; derive the fraction
 * from growth/cost instead. No cost basis or an absurd one ⇒ `null`, which renders "—" (mirrors
 * crm.py `_holding_return`).
 */
export function mapAccountHoldings(raw: RawHolding[], account: AccountRef): Holding[] {
	return raw.map((h) => {
		const cost = num(h.book_cost);
		const growth = num(h.growth);
		const pct = cost > 0 ? growth / cost : null;
		return {
			instrumentId: h.asset_id ? String(h.asset_id) : undefined,
			name: String(h.asset_name ?? h.isin ?? 'Holding'),
			ticker: h.isin ? String(h.isin) : undefined,
			isin: h.isin ? String(h.isin) : undefined,
			sedol: h.sedol ? String(h.sedol) : undefined,
			currency: h.currency ? String(h.currency) : (account.currency ?? 'GBP'),
			wrapper: account.name ?? providerReference(account.uid) ?? 'Account',
			units: num(h.quantity),
			price: num(h.price),
			value: num(h.value),
			bookCost: num(h.book_cost),
			unrealisedGain: growth,
			asOf: h.as_of ? String(h.as_of) : null,
			href: h.isin
				? `/accounts/${account.uid}/transactions?isin=${encodeURIComponent(String(h.isin))}`
				: h.asset_name
					? `/accounts/${account.uid}/transactions?asset=${encodeURIComponent(String(h.asset_name))}`
					: undefined,
			lookthroughHref: h.lookthrough_uid
				? `/look-through/${String(h.lookthrough_uid)}?${new URLSearchParams({
						...(h.lookthrough_as_of ? { as_of: String(h.lookthrough_as_of) } : {}),
						account: String(h.account_uid ?? account.uid)
					}).toString()}`
				: undefined,
			gainPct: pct == null || Math.abs(pct) > 10 ? null : Number(pct.toFixed(4))
		};
	});
}
