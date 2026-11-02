import { providerReference, type Holding } from '@graphene/ui';

type RawHolding = Record<string, unknown>;
const num = (value: unknown) => (typeof value === 'number' ? value : Number(value) || 0);

type AccountRef = {
	uid: string;
	name: string | null;
	currency: string | null;
};

/**
 * Map a lake holdings row the way the platform account record does: gainPct from growth/cost,
 * no or absurd basis ⇒ `null` ("—"). CONSOLIDATION POINT: when a third copy appears, hoist the
 * mapper beside the shared book components (pkgs/ui book/accounts).
 */
export function mapOperateHoldings(
	raw: RawHolding[],
	account: AccountRef,
	tenantUid: string
): Holding[] {
	return raw.map((holding) => {
		const cost = num(holding.book_cost);
		const growth = num(holding.growth);
		const fraction = cost > 0 ? growth / cost : null;
		return {
			instrumentId: holding.asset_id ? String(holding.asset_id) : undefined,
			name: String(holding.asset_name ?? holding.isin ?? 'Holding'),
			ticker: holding.isin ? String(holding.isin) : undefined,
			isin: holding.isin ? String(holding.isin) : undefined,
			sedol: holding.sedol ? String(holding.sedol) : undefined,
			currency: holding.currency ? String(holding.currency) : (account.currency ?? 'GBP'),
			wrapper: account.name ?? providerReference(account.uid) ?? 'Account',
			units: num(holding.quantity),
			price: num(holding.price),
			value: num(holding.value),
			bookCost: cost,
			unrealisedGain: growth,
			asOf: holding.as_of ? String(holding.as_of) : null,
			lookthroughHref: holding.lookthrough_uid
				? `/tenants/${tenantUid}/operate/look-through/${String(holding.lookthrough_uid)}?${new URLSearchParams(
						{
							...(holding.lookthrough_as_of ? { as_of: String(holding.lookthrough_as_of) } : {}),
							account: account.uid
						}
					)}`
				: undefined,
			gainPct: fraction == null || Math.abs(fraction) > 10 ? null : Number(fraction.toFixed(4))
		};
	});
}
