import { error, redirect } from '@sveltejs/kit';
import { humanise } from '@graphene/ui/book/listing';
import { clientName, clientStatus, clientTypeLabel } from '@graphene/ui';
import type { Client } from '$lib/api';
import { formatFrom, stamp } from '$lib/csv-export';
import { requireListingExport } from '$lib/export-auth';
import { exportResponse, moneyCell, shown } from '$lib/export-response';
import { collectAll, exportParams, exportSubtitle, ExportFailed } from '$lib/listing-export';
import { FILTER_KEYS, SORT } from '../listing';
import type { RequestHandler } from './$types';

/** A list as the table's cell reads it (`GIA, ISA`), and semicolon-joined for a spreadsheet. */
const listCell = (items: readonly string[]) =>
	shown(items.join('; '), items.length ? items.join(', ') : '—');

/**
 * The clients listing as a file, honouring whatever the page is currently filtered and sorted to.
 *
 * The PDF is the table: its columns, its names, its order, its text. The workbook and the CSV
 * carry the same columns plus the contact details and counts a spreadsheet is opened for
 * (`sheetOnly`). Where the table renders a component the file renders the fact behind it: the
 * adviser stack becomes the list of names, the status badge its label.
 */
export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	requireListingExport(locals);

	// The listing has an ARCHIVED segment, and it is not one of `FILTER_KEYS` — it selects a
	// different set rather than narrowing this one. Without forwarding it the button on that view
	// would download the ACTIVE book while the screen showed archived clients: a file that
	// contradicts the page it came from, which is the one thing an export must never be.
	const archived =
		url.searchParams.get('segment') === 'closed' || url.searchParams.get('segment') === 'archived';

	// Computed once: the API call and the file's own subtitle must describe the same narrowing.
	const filters = exportParams(url, FILTER_KEYS, SORT);

	let collected;
	try {
		collected = await collectAll<Client>(
			fetch,
			locals.accessToken,
			'/crm/contacts',
			// `/crm/contacts` pages whenever a `limit` is set (app/listing.py::page) — no flag needed.
			filters,
			// Cash and growth are on the row. The file reads the same columns the screen does.
			archived ? { archived: 'true' } : {}
		);
	} catch (err) {
		// Fail the whole export rather than return a short file: a truncated CSV looks complete.
		const status = err instanceof ExportFailed ? err.status : 502;
		error(status === 403 || status === 401 ? status : 502, 'Could not export the clients listing.');
	}

	const rows = collected.rows.map((c) => [
		// The table's own name rule (title, first name, surname), not the record's `name` field.
		clientName(c),
		// The SPECIFIC kind, in the words the column uses — `clientTypeLabel` is shared with the
		// table so the file and the screen cannot disagree about what an organisation is. Falls back
		// to the raw value rather than blanking a kind the vocabulary has not learned yet.
		clientTypeLabel(c.client_type ?? c.type) ?? humanise(c.client_type ?? c.type),
		c.service_type ? humanise(c.service_type) : '',
		c.email,
		c.mobile,
		c.phone,
		// The tags the column shows, every one of them (the table shows the first few and a count).
		listCell(c.account_types ?? []),
		// On screen the tags replaced the count, but a spreadsheet is where someone sums a book, and
		// "GIA; ISA" cannot be added up.
		c.account_count,
		// The table shows a stack of faces; the file shows who they are.
		listCell(c.advisers),
		shown(c.currency ?? 'GBP', c.currency || '—'),
		moneyCell(c.portfolio_value, c.currency),
		moneyCell(c.cash, c.currency),
		moneyCell(c.growth, c.currency, { signed: true }),
		// The listing's OWN rule, not a second one written here. `enabled ? Active : Archived` was
		// two states where the screen shows four: Closed is `deleted_at`, Suspended is the
		// custodian's word, Lead is no accounts, Active is the rest.
		humanise(clientStatus(c))
	]);

	return exportResponse(
		formatFrom(url),
		{
			listing: 'clients',
			filename: `clients${archived ? '-closed' : ''}-${stamp()}`,
			title: 'Clients',
			subtitle: exportSubtitle(collected, filters),
			headers: [
				'Client',
				'Type',
				'Service',
				'Email',
				'Mobile',
				'Phone',
				'Accounts',
				'Account count',
				'Adviser',
				'Currency',
				'Value',
				'Cash',
				'Growth',
				'Status'
			],
			sheetOnly: ['Type', 'Service', 'Email', 'Mobile', 'Phone', 'Account count'],
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
				'number',
				'text',
				'text',
				'money',
				'money',
				'money',
				'text'
			],
			rows
		},
		{ fetch, accessToken: locals.accessToken }
	);
};
