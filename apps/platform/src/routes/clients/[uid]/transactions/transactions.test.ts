import { describe, expect, it } from 'vitest';
import { load } from './+page.server';

const page = (items: unknown[] = []) =>
	new Response(JSON.stringify({ items, total: items.length, facets: {} }), {
		headers: { 'Content-Type': 'application/json' }
	});

describe('the client transactions loader', () => {
	it('loads the real paged ledger with client and date filters, never an adviser facet', async () => {
		let requested = '';
		const result = await load({
			locals: { user: { id: 'user_1' }, accessToken: 'token' },
			// The contact id is a FILTER and comes from the record the layout loaded; the URL
			// carries the uid, which this loader never has to resolve.
			parent: async () => ({ detail: { contact: { id: 42 } } }),
			params: { uid: 'con_9b2e' },
			url: new URL(
				'http://test/clients/con_9b2e/transactions?date_from=2026-01-01&date_to=2026-06-30&adviser=ignored'
			),
			fetch: async (input: string | URL | Request) => {
				requested = String(input);
				return page();
			}
		} as unknown as Parameters<typeof load>[0]);

		expect(requested).toContain('/crm/transactions?contact_id=42');
		expect(requested).toContain('paged=true');
		expect(requested).toContain('date_from=2026-01-01');
		expect(requested).toContain('date_to=2026-06-30');
		expect(requested).not.toContain('adviser=');
		if (!result) throw new Error('the loader redirected, which this case should not');
		expect(result.query).toBeDefined();
		expect(await result.book).toMatchObject({ transactions: [], matchCount: 0, loadError: false });
	});
});
