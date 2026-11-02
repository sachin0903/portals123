import { error, redirect } from '@sveltejs/kit';
import {
	formatPct,
	formatPctPlain,
	holdingColumns,
	holdingMatches,
	sortRows,
	type DataTableSort
} from '@graphene/ui';
import { accountIdFrom, apiUrl, authHeaders } from '$lib/api';
import { mapAccountHoldings } from '$lib/components/accounts/holdings';
import { formatFrom, stamp, type ExportCellType } from '$lib/csv-export';
import { requireListingExport } from '$lib/export-auth';
import { exportResponse, moneyCell, shown } from '$lib/export-response';
import type { RequestHandler } from './$types';

/**
 * One account's holdings, built from the SAME `Holding[]` the band's table renders
 * (`mapAccountHoldings` — also `holdings/+page.server.ts`'s own loader). Mapping the lake's raw
 * rows a second time here is how a column went blank before this route existed in its current
 * form; sharing the one mapping is what keeps the file and the screen from disagreeing again.
 *
 * Not on `collectAll`: holdings are a POSITION SNAPSHOT, not a paged ledger — an account holds
 * tens of positions, not thousands, so `GET /crm/holdings` is read whole.
 *
 * The band's SEARCH, FILTERS and SORT reach this route on the query string (`?q=&class=&currency=
 * &sort=&dir=`): they are local `$state` with nothing in the page URL, so `account-detail.svelte`
 * appends them to the export link. Filtering and ordering use the table's own `holdingMatches` and
 * `holdingColumns`, not a second rule. No `sort` ⇒ the table's own default, value descending.
 */

/** `holdings-table.svelte`'s own starting sort — repeated because a route cannot import a
 *  component's local state. */
const DEFAULT_SORT: DataTableSort = { key: 'value', dir: 'desc' };

export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	requireListingExport(locals);

	// The route is uid-addressed; `?account_id=` is a QUERY FILTER and still keys on the serial the
	// API joins by. A standalone `+server.ts` cannot read its layout's data, so it resolves — the
	// one case `accountIdFrom` still exists for (docs/api.md → "Addressing").
	const accountId = await accountIdFrom(fetch, locals.accessToken, params.uid);
	const query = new URLSearchParams({ account_id: String(accountId) });
	// Every position, unfiltered: Allocation is a share of the whole account, as on screen. The
	// band's filters are applied below, after the base is taken.

	const res = await fetch(`${apiUrl()}/crm/holdings?${query}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) {
		error(
			res.status === 403 || res.status === 401 ? res.status : 502,
			'Could not export this account’s holdings.'
		);
	}
	const payload = (await res.json()) as
		Record<string, unknown>[] | { items: Record<string, unknown>[] };
	const raw = Array.isArray(payload) ? payload : payload.items;

	// The account's own name/currency come off the rows themselves — this standalone route cannot
	// read the layout's `account` (see `accountIdFrom`'s note above), and a second fetch for a
	// label the rows already carry would be a round trip the table's own loader does not pay either.
	const accountName = raw.length ? String(raw[0]?.account_name ?? '') || null : null;
	const holdings = mapAccountHoldings(raw, {
		id: accountId,
		uid: params.uid,
		name: accountName,
		currency: null
	});

	// What the Allocation column is a share OF — every position, never a page of them (there is no
	// paging here to begin with). Mirrors `account-detail.svelte`'s own `allocationBase`.
	const allocationBase = holdings.reduce(
		(sum, h) => sum + (Number.isFinite(h.value) ? h.value : 0),
		0
	);
	// The table only shows Allocation once there is a positive total to be a share OF — an account
	// valued at nothing has no shares of anything (`investment/allocation.ts`).
	const showAllocation = allocationBase > 0;

	const sortKey = url.searchParams.get('sort');
	const sort: DataTableSort | null = sortKey
		? { key: sortKey, dir: url.searchParams.get('dir') === 'asc' ? 'asc' : 'desc' }
		: DEFAULT_SORT;
	const shownRows = holdings.filter((h) =>
		holdingMatches(h, {
			q: url.searchParams.get('q') ?? '',
			assetClass: url.searchParams.get('class') ?? '',
			currency: url.searchParams.get('currency') ?? ''
		})
	);
	const ordered = sortRows(shownRows, holdingColumns({ allocationBase }), sort);

	// The newest as-at across the rows in the file — they are one snapshot, but a custodian can
	// stamp a stale position with an older date than the rest of the snapshot.
	const asOf = ordered.reduce<string | null>((latest, h) => {
		const at = h.asOf ?? null;
		return at && (!latest || at > latest) ? at : latest;
	}, null);
	const accountTitle = accountName ?? `Account ${params.uid}`;

	// THE COLUMNS THE TABLE SHOWS, in its order — `showWrapper={false}` on this account's own band,
	// so Account is a spreadsheet-only column here (it IS on-screen on a client's holdings, which
	// pages several accounts' rows together). `holdings-table.svelte` is the source of truth for
	// every word; Allocation drops out entirely when the table itself would not draw it.
	const headers = [
		'Investment',
		'Account',
		'ISIN',
		'SEDOL',
		'Units',
		'Currency',
		'Price',
		'Book cost',
		'Value',
		...(showAllocation ? ['Allocation'] : []),
		'Unrealised gain',
		'Gain %',
		'As at'
	];
	const types: ExportCellType[] = [
		'text',
		'text',
		'text',
		'text',
		'number',
		'text',
		'money',
		'money',
		'money',
		...(showAllocation ? (['number'] as const) : []),
		'money',
		'number',
		'date'
	];

	const rows = ordered.map((h) => {
		const currency = h.currency || 'GBP';
		const share = showAllocation ? h.value / allocationBase : null;
		// `moneyCell` itself renders '—' for a null amount, but the TABLE drops the amount and the
		// " · " connector entirely in that case (`holdings-table.svelte`'s `gainPct` cell) rather
		// than showing "— · …" — `hasGain` reproduces that same branch, not a second formatter.
		const hasGain = h.unrealisedGain != null;
		const gainMoney = moneyCell(h.unrealisedGain, currency, { signed: true });
		const gainPctText = h.gainPct == null ? '—' : formatPct(h.gainPct);
		return [
			h.name,
			h.wrapper,
			shown(h.isin ?? '', h.isin || '—'),
			shown(h.sedol ?? '', h.sedol || '—'),
			shown(h.units, h.units.toLocaleString('en-GB')),
			shown(h.currency ?? '', h.currency || '—'),
			moneyCell(h.price, currency),
			moneyCell(h.bookCost, currency),
			moneyCell(h.value, currency),
			...(showAllocation
				? [
						shown(
							share == null ? null : Number((share * 100).toFixed(2)),
							share == null ? '—' : formatPctPlain(share)
						)
					]
				: []),
			// SAME CELL the table renders (`holdings-table.svelte`'s `gainPct` column): the signed
			// amount, " · ", then the percentage — reused rather than re-derived, so the PDF cannot
			// drift from the screen even where the screen's own figure is a surprising one.
			shown(gainMoney.value, hasGain ? `${gainMoney.text} · ${gainPctText}` : gainPctText),
			// `gainPct` is a fraction; a column headed "Gain %" holds the percentage.
			h.gainPct == null ? null : Number((h.gainPct * 100).toFixed(2)),
			h.asOf ?? ''
		];
	});

	return exportResponse(
		formatFrom(url),
		{
			listing: 'holdings',
			filename: `account-${params.uid}-holdings-${stamp()}`,
			title: `${accountTitle}: holdings`,
			subtitle: asOf
				? `${ordered.length.toLocaleString()} positions, as at ${asOf}`
				: `${ordered.length.toLocaleString()} positions`,
			headers,
			types,
			sheetOnly: ['Account', 'Gain %', 'As at'],
			rows
		},
		{ fetch, accessToken: locals.accessToken }
	);
};
