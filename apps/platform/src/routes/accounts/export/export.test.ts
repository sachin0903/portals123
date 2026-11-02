import { describe, expect, it } from 'vitest';
import { GET } from './+server';

const ACCOUNT = {
	id: 12,
	uid: 'seccl_AXFF45915',
	name: 'Theo Caldwell ISA',
	status: 'active',
	currency: 'GBP',
	value: 125000,
	contact_id: 5,
	risk_level: null,
	custody: null,
	opened_at: '2024-01-15',
	product_name: 'Stocks & Shares ISA',
	product_type: 'isa',
	discretionary: true,
	service_type: 'discretionary',
	model_id: 3,
	model_name: 'Balanced Growth',
	adviser: 'Zoe Lead',
	client: {
		id: 5,
		uid: 'c5',
		type: 'individual',
		name: null,
		title: null,
		first_name: 'Theo',
		surname: 'Caldwell'
	},
	cash: 1200,
	growth: 4300
};

/** Drive the route, recording every URL it asked the API for. */
async function run(href: string, body: unknown = { items: [ACCOUNT], total: 1, facets: {} }) {
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
			return Response.json(body);
		}
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
	} as any);
	return { response, requested };
}

describe('accounts export', () => {
	it('sends the PDF only the table: its columns, in order, as the cells read', async () => {
		const response = await GET({
			locals: {
				user: { id: 'u1' },
				accessToken: 'tok',
				permissions: ['listings:export'],
				role: null
			},
			url: new URL('http://test/accounts/export?format=pdf'),
			fetch: async () => Response.json({ items: [ACCOUNT], total: 1, facets: {} })
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
		} as any);
		const table = await response.json();
		// The table's own eleven columns (`accounts-listing.svelte`'s `columns`) — Reference,
		// Product, Discretionary and Opened are sheet-only and must not reach the PDF.
		expect(table.headers).toEqual([
			'Account',
			'Client',
			'Adviser',
			'Service type',
			'Type',
			'Model',
			'Currency',
			'Value',
			'Cash',
			'Growth',
			'Status'
		]);
		expect(table.rows[0]).toEqual([
			'Theo Caldwell ISA',
			'Theo Caldwell',
			'Zoe Lead',
			'Discretionary',
			'Isa',
			'Balanced Growth',
			'GBP',
			'£125,000.00',
			'£1,200.00',
			'+£4,300.00',
			'Active'
		]);
	});

	it('keeps the table’s default order when no sort is chosen', async () => {
		const { requested } = await run('http://test/accounts/export');
		expect(requested[0]).toContain('sort=name');
		expect(requested[0]).toContain('dir=asc');
	});
});
