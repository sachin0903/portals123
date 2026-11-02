import { error, redirect } from '@sveltejs/kit';
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
 * The transactions ledger as CSV/Excel/PDF, honouring whatever the page is currently filtered to.
 *
 * Headers are in the TABLE's own order (`transactions-listing.svelte`'s `columns`, with Client and
 * Account both shown here — the firm-wide ledger is the one mount that keeps both). Adviser,
 * Account reference and Method are extra: the table has never shown them, so they are `sheetOnly`,
 * interleaved where they read naturally, and the PDF drops them.
 *
 * Type and Subtype are ONE column here, as they are on screen: on this book most rows carry the
 * same word in both, and `subtypeDetail` is what decides whether the vendor's own word says
 * anything the canonical type has not already said. Splitting them for the file would put the
 * redundancy back, a column at a time — and would disagree with the table it came from.
 */
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	requireListingExport(locals);

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
			// Unlike contacts and accounts, this one pages only when asked (`paged: bool = False`).
			{ paged: 'true' }
		);
	} catch (err) {
		// Fail the whole export rather than return a short file: a truncated CSV looks complete.
		const status = err instanceof ExportFailed ? err.status : 502;
		error(
			status === 403 || status === 401 ? status : 502,
			'Could not export the transactions ledger.'
		);
	}

	const rows = collected.rows.map((t) => {
		const detail = subtypeDetail(t);
		const type = `${humanise(t.type)}${detail ? `: ${detail}` : ''}`;
		return [
			shown(t.date, formatDate(t.date)),
			// The reference AS THE TABLE SHOWS IT — the provider prefix is namespacing, not part of
			// the reference someone quotes back to a custodian.
			transactionReference(t.uid),
			t.client_name,
			t.adviser ?? '',
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
			// UNSIGNED, as the ledger shows it: `platform.transactions` carries direction in `type`,
			// not on the figure, so summing this column does NOT give a net movement — it adds
			// withdrawals to deposits. See commit 0383d3c3.
			moneyCell(t.amount, t.currency),
			humanise(t.status)
		];
	});

	// One spec, every container — so the CSV, the workbook and the PDF of this listing can never
	// disagree about which columns it has.
	return exportResponse(
		formatFrom(url),
		{
			listing: 'transactions',
			filename: `transactions-${stamp()}`,
			title: 'Transactions',
			subtitle: exportSubtitle(collected, filters),
			// THE COLUMNS THE TABLE SHOWS, in its order — Adviser, Account reference and Method
			// interleaved for the spreadsheet (`sheetOnly`, below), where they read naturally.
			headers: [
				'Date',
				'Reference',
				'Client',
				'Adviser',
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
				'text',
				'text',
				'number',
				'text',
				'text',
				'money',
				'text'
			],
			sheetOnly: ['Adviser', 'Account reference', 'Method'],
			rows
		},
		{ fetch, accessToken: locals.accessToken }
	);
};
