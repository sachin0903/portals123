import { error, redirect } from '@sveltejs/kit';
import { accountIdFrom } from '$lib/api';
import { formatDate } from '@graphene/ui';
import { humanise } from '@graphene/ui/book/listing';
import { subtypeDetail, transactionReference, type Transaction } from '@graphene/ui';
import { formatFrom, stamp } from '$lib/csv-export';
import { requireListingExport } from '$lib/export-auth';
import { exportResponse, moneyCell, shown } from '$lib/export-response';
import { collectAll, exportParams, exportSubtitle, ExportFailed } from '$lib/listing-export';
import { FILTER_KEYS, SORT } from '../listing';
import type { RequestHandler } from './$types';

/**
 * One account's transactions, honouring whatever the band is filtered to.
 *
 * `FILTER_KEYS`/`SORT` come from the band's own `listing.ts`, which re-exports the record layout's
 * `_TRANSACTION_FILTERS` — the band, its loader and this file then cannot disagree about what a
 * filter is or which order is the default, which is how an export quietly stops matching the table.
 *
 * No Account column: every row belongs to the account this file is OF, and the account is named in
 * the filename and the PDF's own heading. The client-level and firm-level exports carry it because
 * there it distinguishes rows.
 */
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	requireListingExport(locals);

	// The route is uid-addressed; `?account_id=` is a QUERY FILTER and still keys on the serial
	// the API joins by. A standalone `+server.ts` cannot read its layout's data, so it resolves —
	// the one case `accountIdFrom` still exists for (docs/api.md → "Addressing").
	const accountId = await accountIdFrom(fetch, locals.accessToken, params.uid);

	// Computed once: the API call and the file's own subtitle must describe the same narrowing.
	// SORT, so a URL with no `sort` takes the table's own default order rather than the API's.
	const filters = exportParams(url, FILTER_KEYS, SORT);
	// The holding DRILL-DOWN, which is not a listing filter and so is not in `_TRANSACTION_FILTERS`
	// — clicking a position on the holdings band lands here with `?isin=` (or `?asset=` when the
	// instrument has no ISIN) and the band shows that one security, under a banner saying so.
	// Without this the export ignored it and wrote the account's whole ledger: a file that answers
	// a different question from the screen it was taken from, and looks complete doing it.
	// Mirrors `accounts/[uid]/transactions/+page.server.ts` exactly, `q` fallback included.
	const isin = url.searchParams.get('isin');
	const asset = url.searchParams.get('asset');
	if (isin) filters.set('isin', isin);
	else if (asset) filters.set('q', asset);

	let collected;
	try {
		collected = await collectAll<Transaction>(
			fetch,
			locals.accessToken,
			'/crm/transactions',
			filters,
			{ paged: 'true', account_id: String(accountId) }
		);
	} catch (err) {
		// Fail the whole export rather than return a short file: a truncated one looks complete.
		const status = err instanceof ExportFailed ? err.status : 502;
		error(
			status === 403 || status === 401 ? status : 502,
			'Could not export this account’s transactions.'
		);
	}

	const accountName = collected.rows[0]?.account_name ?? `Account ${params.uid}`;
	const rows = collected.rows.map((t) => {
		const detail = subtypeDetail(t);
		const type = `${humanise(t.type)}${detail ? `: ${detail}` : ''}`;
		return [
			shown(t.date, formatDate(t.date)),
			// The reference AS THE TABLE SHOWS IT — the provider prefix is namespacing, not part of
			// the reference someone quotes back to a custodian.
			transactionReference(t.uid),
			// The category chip the table sets beside the type, as words after it.
			shown(type, t.category ? `${type} · ${humanise(t.category)}` : type),
			t.asset ?? 'Cash',
			shown(t.quantity, t.quantity == null ? '—' : t.quantity.toLocaleString('en-GB')),
			shown(t.isin ?? '', t.isin || '—'),
			shown(t.currency ?? '', t.currency || '—'),
			// Unsigned, as the ledger shows it — `platform.transactions` stores no direction on the
			// figure, so this column does not sum to a net movement. Type says which way it went.
			moneyCell(t.amount, t.currency),
			humanise(t.status)
		];
	});

	return exportResponse(
		formatFrom(url),
		{
			listing: 'account-transactions',
			filename: `account-${params.uid}-transactions-${stamp()}`,
			title: `${accountName}: transactions`,
			subtitle: exportSubtitle(collected, filters),
			// THE COLUMNS THE TABLE SHOWS, in its order. This drifted the moment the ledger gained Units,
			// ISIN and Currency and the export did not: the file still carried Method, a column the table
			// does not show, so it was a different document about the same rows.
			// `book-accounts.ssr.test.ts` and `export.test.ts` now pin the two together.
			headers: [
				'Date',
				'Reference',
				'Type',
				'Asset',
				'Units',
				'ISIN',
				'Currency',
				'Amount',
				'Status'
			],
			// Declared, never inferred: this book's references include `000839969`, and
			// "all digits means number" would strip the leading zeros off a custodian's own id.
			types: ['date', 'text', 'text', 'text', 'number', 'text', 'text', 'money', 'text'],
			rows
		},
		{ fetch, accessToken: locals.accessToken }
	);
};
