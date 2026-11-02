import { readFile } from 'node:fs/promises';
import { describe, expect, it } from 'vitest';
import { actions } from './+page.server';

const PAGE = await readFile(new URL('./+page.svelte', import.meta.url), 'utf8');

describe('firm currency settings', () => {
	it('presents one allow-list and a base chosen from it', () => {
		expect(PAGE).toContain('name="allowed_currencies"');
		expect(PAGE).toContain('name="base_currency"');
		expect(PAGE).toContain('allowedCurrencyOptions');
		expect(PAGE).toContain('It must remain in the allowed list.');
	});

	it('sends every allowed currency and the base to the target firm endpoint', async () => {
		const data = new FormData();
		data.append('base_currency', 'EUR');
		data.append('allowed_currencies', 'EUR');
		data.append('allowed_currencies', 'GBP');
		let request: { url: string; method: string; body: unknown } | undefined;

		// SvelteKit's generated Action type is wider than this focused event double.
		const result = await actions.setCurrencies!({
			params: { uid: 't_firm' },
			locals: {
				user: { id: 'ops' },
				accessToken: 'token',
				permissions: ['tenants:write'],
				role: null
			},
			request: { formData: async () => data },
			fetch: async (input: RequestInfo | URL, init?: RequestInit) => {
				request = {
					url: String(input),
					method: String(init?.method),
					body: JSON.parse(String(init?.body))
				};
				return Response.json({});
			}
		} as never);

		expect(result).toMatchObject({ action: 'setCurrencies', ok: true });
		expect(request).toMatchObject({
			url: expect.stringContaining('/admin/tenants/t_firm/currency-settings'),
			method: 'PUT',
			body: { base_currency: 'EUR', allowed_currencies: ['EUR', 'GBP'] }
		});
	});
});
