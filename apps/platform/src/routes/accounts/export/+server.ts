import { error, redirect } from '@sveltejs/kit';
import { humanise } from '@graphene/ui/book/listing';
import { clientName, providerReference, serviceTypeLabel } from '@graphene/ui';
import type { Account } from '$lib/api';
import { formatFrom, stamp } from '$lib/csv-export';
import { requireListingExport } from '$lib/export-auth';
import { exportResponse, moneyCell, shown } from '$lib/export-response';
import { collectAll, exportParams, exportSubtitle, ExportFailed } from '$lib/listing-export';
import { FILTER_KEYS, SORT } from '../listing.js';
import type { RequestHandler } from './$types';

/** Nothing reads as an em dash on screen (`accounts-listing.svelte`'s cells); nothing reads as a
 *  blank cell in a sheet. One helper so every dashed column agrees on both. */
const dash = (text: string | null | undefined) => shown(text ?? '', text ?? '—');

/**
 * The accounts listing as CSV/Excel/PDF, honouring whatever the page is currently filtered to.
 *
 * `headers` is in the TABLE's own order and words (`accounts-listing.svelte`'s `columns`), with
 * the sheet-only extras — Reference, Product, Discretionary, Opened — interleaved where they read
 * naturally, so the PDF (which drops `sheetOnly`, see `onScreen`) shows exactly the table's eleven
 * columns while the spreadsheet keeps the lot.
 */
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	requireListingExport(locals);

	// Computed once: the API call and the file's own subtitle must describe the same narrowing.
	// `SORT` is the loader's own default — without it a URL with no `sort` took the API's order,
	// not the table's.
	const filters = exportParams(url, FILTER_KEYS, SORT);

	let collected;
	try {
		collected = await collectAll<Account>(fetch, locals.accessToken, '/crm/accounts', filters);
	} catch (err) {
		// Fail the whole export rather than return a short file: a truncated CSV looks complete.
		const status = err instanceof ExportFailed ? err.status : 502;
		error(
			status === 403 || status === 401 ? status : 502,
			'Could not export the accounts listing.'
		);
	}

	const rows = collected.rows.map((a) => [
		// The table's own fallback for an unnamed account: the provider reference, then 'Account'.
		shown(a.name ?? '', a.name ?? providerReference(a.uid) ?? 'Account'),
		// Sheet-only: the reference the table shows in small type under the name.
		a.uid,
		dash(a.client.uid == null ? null : clientName(a.client)),
		// `AdviserStack` with one name shows it, or the dash, exactly like this.
		dash(a.adviser && a.adviser.trim() !== '' ? a.adviser : null),
		dash(serviceTypeLabel(a.service_type)),
		dash(a.product_type ? humanise(a.product_type) : null),
		// Sheet-only: the specific product, where Type is only its category.
		a.product_name ?? '',
		dash(a.model_name),
		// Sheet-only: whether the account is run to a model — a narrower fact than the service type.
		a.discretionary ? 'Yes' : 'No',
		dash(a.currency),
		moneyCell(a.value, a.currency),
		moneyCell(a.cash, a.currency),
		moneyCell(a.growth, a.currency, { signed: true }),
		humanise(a.status),
		// Sheet-only.
		a.opened_at ?? ''
	]);

	// One spec, every container — so the CSV, the workbook and the PDF of this listing can never
	// disagree about what is in it.
	return exportResponse(
		formatFrom(url),
		{
			listing: 'accounts',
			filename: `accounts-${stamp()}`,
			title: 'Accounts',
			subtitle: exportSubtitle(collected, filters),
			headers: [
				'Account',
				'Reference',
				'Client',
				'Adviser',
				'Service type',
				'Type',
				'Product',
				'Model',
				'Discretionary',
				'Currency',
				'Value',
				'Cash',
				'Growth',
				'Status',
				'Opened'
			],
			sheetOnly: ['Reference', 'Product', 'Discretionary', 'Opened'],
			// Declared, never inferred: this book's references include `000839969`, and
			// "all digits means number" would strip the leading zeros off a custodian's own id.
			types: [
				'text',
				'text',
				'text',
				'text',
				'text',
				'text',
				'text',
				'text',
				'text',
				'text',
				'money',
				'money',
				'money',
				'text',
				'date'
			],
			rows
		},
		{ fetch, accessToken: locals.accessToken }
	);
};
