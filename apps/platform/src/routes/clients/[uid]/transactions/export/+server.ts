import { error, redirect } from '@sveltejs/kit';
import { contactIdFrom } from '$lib/api';
import { formatDate } from '@graphene/ui';
import { humanise } from '@graphene/ui/book/listing';
import {
	providerReference,
	subtypeDetail,
	transactionReference,
	type Transaction
} from '@graphene/ui';
import { formatFrom, stamp } from '$lib/csv-export';
import { requireListingExport } from '$lib/export-auth';
import { exportResponse, moneyCell, shown } from '$lib/export-response';
import { collectAll, exportParams, exportSubtitle, ExportFailed } from '$lib/listing-export';
import { FILTER_KEYS, SORT } from '../listing';
import type { RequestHandler } from './$types';

/**
 * One client's transactions, across their accounts, honouring the page's filters.
 *
 * Rewritten onto the shared export machinery. It used to page the API by hand and carry its own
 * `csvCell` — one that quoted every value and had NO formula guard, so a client name beginning
 * `=` executed when the file was opened. That is the whole argument for one writer: this route
 * was the oldest export in the product and the only one still exposed.
 *
 * Account IS a column here, unlike the account-level export: these rows span several, and which
 * one a movement belongs to is the question this page exists to answer.
 *
 * Headers are in the TABLE's own order. Account reference and Method are extra — the table has
 * never shown them — so they are `sheetOnly`, interleaved where they read naturally, and the PDF
 * drops them.
 */
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	requireListingExport(locals);

	// The route is uid-addressed; `?contact_id=` is a QUERY FILTER on `/crm/transactions` and
	// stays the serial the API keys its join on. A standalone `+server.ts` cannot read the
	// layout's data, so it resolves — the one case the resolver still exists for.
	const contactId = await contactIdFrom(fetch, locals.accessToken, params.uid);

	// Computed once: the API call and the file's own subtitle must describe the same narrowing.
	// SORT, so a URL with no `sort` takes the table's own default order rather than the API's.
	const filters = exportParams(url, FILTER_KEYS, SORT);

	let collected;
	try {
		collected = await collectAll<Transaction>(
			fetch,
			locals.accessToken,
			'/crm/transactions',
			filters,
			{ paged: 'true', contact_id: String(contactId) }
		);
	} catch (err) {
		// Fail the whole export rather than return a short file: a truncated one looks complete.
		const status = err instanceof ExportFailed ? err.status : 502;
		error(
			status === 403 || status === 401 ? status : 502,
			'Could not export this client’s transactions.'
		);
	}

	const clientName = collected.rows[0]?.client_name ?? `Client ${params.uid}`;
	const rows = collected.rows.map((t) => {
		const detail = subtypeDetail(t);
		const type = `${humanise(t.type)}${detail ? `: ${detail}` : ''}`;
		return [
			shown(t.date, formatDate(t.date)),
			// The reference AS THE TABLE SHOWS IT — the provider prefix is namespacing, not part of
			// the reference someone quotes back to a custodian.
			transactionReference(t.uid),
			// The name with the provider reference under it, as the table's cell stacks them.
			shown(
				t.account_name,
				[t.account_name, providerReference(t.account_uid)].filter(Boolean).join('\n')
			),
			t.account_uid,
			// The category chip the table sets beside the type, as words after it.
			shown(type, t.category ? `${type} · ${humanise(t.category)}` : type),
			t.method ? humanise(t.method) : '',
			t.asset ?? 'Cash',
			shown(t.quantity, t.quantity == null ? '—' : t.quantity.toLocaleString('en-GB')),
			shown(t.isin ?? '', t.isin || '—'),
			shown(t.currency ?? '', t.currency || '—'),
			// UNSIGNED, as the ledger shows it: direction lives in `type`, not on the figure, so this
			// column does not sum to a net movement. See commit 0383d3c3.
			moneyCell(t.amount, t.currency),
			humanise(t.status)
		];
	});

	return exportResponse(
		formatFrom(url),
		{
			listing: 'client-transactions',
			filename: `client-${params.uid}-transactions-${stamp()}`,
			title: `${clientName}: transactions`,
			subtitle: exportSubtitle(collected, filters),
			// THE COLUMNS THE TABLE SHOWS, in its order — Account reference and Method interleaved for
			// the spreadsheet (`sheetOnly`, below), where they read naturally.
			headers: [
				'Date',
				'Reference',
				'Account',
				'Account reference',
				'Type',
				'Method',
				'Asset',
				'Units',
				'ISIN',
				'Currency',
				'Amount',
				'Status'
			],
			// Declared, never inferred: this book's references include `000839969`, and
			// "all digits means number" would strip the leading zeros off a custodian's own id.
			types: [
				'date',
				'text',
				'text',
				'text',
				'text',
				'text',
				'text',
				'number',
				'text',
				'text',
				'money',
				'text'
			],
			sheetOnly: ['Account reference', 'Method'],
			rows
		},
		{ fetch, accessToken: locals.accessToken }
	);
};
