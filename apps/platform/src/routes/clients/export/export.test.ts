import { describe, expect, it } from 'vitest';
import { GET } from './+server';

const CLIENT = {
	id: 1,
	uid: 'c1',
	type: 'individual',
	service_type: 'advised',
	enabled: true,
	name: 'Theo Caldwell',
	title: null,
	first_name: 'Theo',
	surname: 'Caldwell',
	email: 'theo@example.com',
	mobile: '07700 900000',
	phone: '0117 900 0000',
	address: null,
	portfolio_value: 325000,
	account_count: 2,
	advisers: ['Zoe Lead', 'Ann Rivers'],
	cash: 1200,
	growth: 4300
};

/** How many CSV fields one line holds, honouring quotes — so a comma a firm typed into a phone
 *  label is counted as part of its cell rather than as a column boundary. */
function countFields(line: string): number {
	let fields = 1;
	let quoted = false;
	for (const ch of line) {
		if (ch === '"') quoted = !quoted;
		else if (ch === ',' && !quoted) fields += 1;
	}
	return fields;
}

/** Drive the route, recording every URL it asked for. */
async function run(href: string, body: unknown = { items: [CLIENT], total: 1, facets: {} }) {
	const requested: string[] = [];
	const response = await GET({
		locals: {
			user: { id: 'u1' },
			accessToken: 'tok',
			permissions: ['listings:export'],
			role: null
		},
		url: new URL(href),
		fetch: async (input: string | URL | Request) => {
			requested.push(String(input));
			return new Response(JSON.stringify(body), {
				headers: { 'Content-Type': 'application/json' }
			});
		}
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
	} as any);
	return { response, requested, text: await response.text() };
}

describe('clients CSV export', () => {
	it('carries the listing filters into the API call', async () => {
		const { requested } = await run(
			'http://test/clients/export?q=cald&status=active&adviser=Zoe%20Lead'
		);
		expect(requested[0]).not.toContain('metrics=true');
		expect(requested[0]).toContain('q=cald');
		expect(requested[0]).toContain('status=active');
		expect(requested[0]).toContain('adviser=Zoe+Lead');
	});

	it('starts at the beginning, whatever page you were on', async () => {
		// The export is the match set, not the page — landing at row 75 because that is where the
		// reader happened to be would be a silently short file.
		const { requested } = await run('http://test/clients/export?offset=75');
		expect(requested[0]).toContain('offset=0');
		expect(requested[0]).not.toContain('offset=75');
	});

	it('follows the CLOSED segment', async () => {
		// `segment` is not a filter — it selects a different set. Without this the button on the
		// Closed view downloads the ACTIVE book, a file that contradicts the page it came from.
		const { requested, response } = await run('http://test/clients/export?segment=closed');
		expect(requested[0]).toContain('archived=true');
		expect(response.headers.get('content-disposition')).toContain('clients-closed-');
	});

	it('still accepts the legacy segment=archived deep link', async () => {
		const { requested, response } = await run('http://test/clients/export?segment=archived');
		expect(requested[0]).toContain('archived=true');
		expect(response.headers.get('content-disposition')).toContain('clients-closed-');
	});

	it('does not claim to be the archive when it is not', async () => {
		const { requested, response } = await run('http://test/clients/export');
		expect(requested[0]).not.toContain('archived=');
		expect(response.headers.get('content-disposition')).toMatch(
			/filename="clients-\d{4}-\d{2}-\d{2}\.csv"/
		);
	});

	it('writes the columns the table shows, flattening what it renders', async () => {
		const { text } = await run('http://test/clients/export');
		const [header, row] = text.replace('﻿', '').trim().split('\r\n');
		// The table's columns in the table's order, with the spreadsheet's extras between them.
		expect(header).toBe(
			'Client,Type,Service,Email,Mobile,Phone,Accounts,Account count,Adviser,Currency,Value,Cash,Growth,Status'
		);
		// The table draws a stack of avatars; the file names them. Joined with a SEMICOLON, which is
		// not a CSV delimiter — so the cell needs no quoting and stays readable in a text editor.
		expect(row).toContain('Zoe Lead; Ann Rivers');
		expect(row).not.toContain('"Zoe Lead');
		expect(row).toContain('Theo Caldwell');
		expect(row).toContain('Active');
		expect(row).toContain('07700 900000');
		expect(row).toContain('0117 900 0000');
	});

	it('sends the PDF only the table: its columns, in order, as the cells read', async () => {
		const response = await GET({
			locals: { user: { id: 'u1' }, accessToken: 'tok', permissions: ['listings:export'] },
			url: new URL('http://test/clients/export?format=pdf'),
			fetch: async () =>
				Response.json({ items: [{ ...CLIENT, account_types: ['GIA', 'ISA'] }], total: 1 })
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
		} as any);
		const table = await response.json();
		expect(table.headers).toEqual([
			'Client',
			'Accounts',
			'Adviser',
			'Currency',
			'Value',
			'Cash',
			'Growth',
			'Status'
		]);
		expect(table.rows[0]).toEqual([
			'Theo Caldwell',
			'GIA, ISA',
			'Zoe Lead, Ann Rivers',
			'—',
			'£325,000.00',
			'£1,200.00',
			'+£4,300.00',
			'Active'
		]);
	});

	it('keeps the table’s default order when no sort is chosen', async () => {
		const { requested } = await run('http://test/clients/export');
		expect(requested[0]).toContain('sort=name');
		expect(requested[0]).toContain('dir=asc');
	});

	it('quotes a phone cell rather than letting a comma split the row', async () => {
		const { text } = await run('http://test/clients/export', {
			items: [{ ...CLIENT, phone: '0117 900 0002, ext 4' }],
			total: 1,
			facets: {}
		});
		const [header, row] = text.replace('﻿', '').trim().split('\r\n');
		expect(row).toContain('"0117 900 0002, ext 4"');
		// And the row still has exactly as many fields as the header once the quoting is honoured —
		// the whole point of the quotes. A naive split on every comma would find one field too many.
		expect(countFields(row)).toBe(header.split(',').length);
		expect(row.split(',')).toHaveLength(header.split(',').length + 1);
	});

	it('leaves the phone cell empty when the firm holds none', async () => {
		const { text } = await run('http://test/clients/export', {
			items: [
				{ ...CLIENT, phone: null },
				{ ...CLIENT, phone: undefined }
			],
			total: 3,
			facets: {}
		});
		const rows = text.replace('﻿', '').trim().split('\r\n').slice(1);
		for (const row of rows) expect(row).toContain('theo@example.com,07700 900000,,');
	});

	it('passes a refusal through as a refusal', async () => {
		// A 403 reported as a gateway error tells someone their export is broken when the truth is
		// they may not have it.
		await expect(
			GET({
				locals: {
					user: { id: 'u1' },
					accessToken: 'tok',
					permissions: ['listings:export'],
					role: null
				},
				url: new URL('http://test/clients/export'),
				fetch: async () => new Response('', { status: 403 })
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
			} as any)
		).rejects.toMatchObject({ status: 403 });
	});

	it('refuses a caller without listings:export before it fetches anything', async () => {
		let fetched = false;
		await expect(
			GET({
				locals: {
					user: { id: 'u1' },
					accessToken: 'tok',
					permissions: ['contacts:read'],
					role: null
				},
				url: new URL('http://test/clients/export'),
				fetch: async () => {
					fetched = true;
					return new Response('{}');
				}
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
			} as any)
		).rejects.toMatchObject({ status: 403 });
		expect(fetched).toBe(false);
	});

	it('uses the listing’s own derived status, not enabled/archived', async () => {
		const { text } = await run('http://test/clients/export', {
			items: [
				{ ...CLIENT, account_count: 2, enabled: true },
				{ ...CLIENT, account_count: 0, enabled: true },
				{ ...CLIENT, account_count: 2, provider_status: 'suspended' }
			],
			total: 3,
			facets: {}
		});
		const [, ...rows] = text.replace('﻿', '').trim().split(String.fromCharCode(13, 10));
		expect(rows[0].endsWith('Active')).toBe(true);
		expect(rows[1].endsWith('Lead')).toBe(true);
		expect(rows[2].endsWith('Suspended')).toBe(true);
		expect(text).not.toContain('Archived');
		expect(text).not.toContain('Inactive');
	});
});
