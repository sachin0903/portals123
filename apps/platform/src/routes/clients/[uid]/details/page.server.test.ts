/**
 * Details fetches the fat record itself — and the client-book pin, inside the same streamed
 * record — and does not pull picker lists on first paint.
 */
import { describe, expect, it } from 'vitest';
import { load } from './+page.server';

const locals = {
	user: { id: 'u_1' },
	accessToken: 'tok',
	permissions: ['contacts:write', 'illustrations:write'],
	role: 'adviser'
} as unknown as App.Locals;

describe('the Details load', () => {
	it('fetches the fat contact and not members, models or countries', async () => {
		const urls: string[] = [];
		const fetch = (async (url: string) => {
			const href = String(url);
			urls.push(href);
			const body = href.includes('/reviews/policies')
				? []
				: href.includes('/reviews/pending')
					? []
					: href.endsWith('/crm/classifications')
						? [{ uid: 'cls_1', name: 'Advised', slug: 'advised' }]
						: href.endsWith('/classification')
							? {
									classification: {
										uid: 'cls_1',
										name: 'Advised',
										slug: 'advised',
										source: 'matched'
									}
								}
							: { contact: { id: 42, uid: 'con_9b2e' }, accounts: [], kyc: [] };
			return new Response(JSON.stringify(body), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof globalThis.fetch;

		const result = (await load({
			locals,
			params: { uid: 'con_9b2e' },
			fetch
		} as never)) as {
			record: Promise<{
				detail: { contact: { id: number } };
				classifications: { uid: string }[];
				resolvedClassification: { uid: string; source: string } | null;
			}>;
		};
		const record = await result.record;

		expect(urls.some((u) => /\/crm\/contacts\/con_9b2e$/.test(u))).toBe(true);
		expect(urls.some((u) => u.includes('/reviews/pending'))).toBe(true);
		expect(urls.some((u) => u.includes('/reviews/policies'))).toBe(true);
		// The pin — the layout's former second stretch — is read here, behind the first byte.
		expect(urls.some((u) => u.endsWith('/crm/classifications'))).toBe(true);
		expect(urls.some((u) => u.endsWith('/crm/contacts/con_9b2e/classification'))).toBe(true);
		expect(urls.some((u) => u.includes('/firm/members'))).toBe(false);
		expect(urls.some((u) => u.includes('/crm/models'))).toBe(false);
		expect(urls.some((u) => u.includes('/ref/countries'))).toBe(false);
		expect(record.detail.contact.id).toBe(42);
		expect(record.classifications).toEqual([{ uid: 'cls_1', name: 'Advised', slug: 'advised' }]);
		expect(record.resolvedClassification).toMatchObject({ uid: 'cls_1', source: 'matched' });
	});
});
