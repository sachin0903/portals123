import { redirect } from '@sveltejs/kit';
import { authHeaders } from '$lib/api';
import { bookUrl } from '$lib/book/api';
import type { Holding, Transaction } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import type { PageServerLoad } from './$types';

/** Raw lake holding shape (see crm.py list-holdings). */
type RawHolding = Record<string, unknown>;
const num = (x: unknown): number => (typeof x === 'number' ? x : Number(x) || 0);

/**
 * One account of the target firm — the platform's `/accounts/[id]` overview + ledger, stacked,
 * with `?tenant=` on both reads. The holdings mapping is a copy of the platform's (inline there
 * too): the lake's `growth_pct` is a percent that reads as +6,303% once formatted, so the
 * fraction is re-derived from growth/cost and absurd bases dropped. CONSOLIDATION POINT: when a
 * third copy appears, hoist the mapper beside the shared book components (pkgs/ui book/accounts).
 *
 * The transactions are the FIRM'S now. This read served the shared fixture corpus filtered by
 * `account_id`, which meant an operator looking at any firm's account saw the same invented rows —
 * worse than blank, because invented rows are indistinguishable from a real ledger. `?tenant=`
 * scopes `/crm/transactions` to the target firm exactly as it scopes the holdings.
 */
export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, account } = await parent();
	const headers = authHeaders(locals.accessToken);

	const [holdingsRes, txRes] = await Promise.all([
		fetch(bookUrl('/crm/holdings', tenant.uid, { account_id: String(account.id) }), { headers }),
		fetch(
			bookUrl('/crm/transactions', tenant.uid, {
				account_id: String(account.id),
				limit: String(PAGE_SIZE)
			}),
			{ headers }
		)
	]);
	const raw: RawHolding[] = holdingsRes.ok ? await holdingsRes.json() : [];
	const holdings: Holding[] = raw.map((h) => {
		const cost = num(h.book_cost);
		const growth = num(h.growth);
		const pct = cost > 0 ? growth / cost : null;
		return {
			name: String(h.asset_name ?? h.isin ?? 'Holding'),
			ticker: h.isin ? String(h.isin) : undefined,
			wrapper: account.name ?? account.uid ?? 'Account',
			units: num(h.quantity),
			price: num(h.price),
			value: num(h.value),
			gainPct: pct == null || Math.abs(pct) > 10 ? null : Number(pct.toFixed(4))
		};
	});

	const transactions: Transaction[] = txRes.ok ? await txRes.json() : [];

	return { holdings, transactions, loadError: !holdingsRes.ok || !txRes.ok };
};
