/**
 * The client-book pin read — the two best-effort calls Overview and Details fold into their
 * streamed record. What matters: the list is only asked for when the caller may read contacts,
 * and no failure on either call can fail the record.
 */
import { describe, expect, it } from 'vitest';
import { loadClientBookPin } from './classification';

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

const BOOK = { uid: 'cls_1', name: 'Advised', slug: 'advised' };

function harness(handler: (url: string) => Response) {
	const urls: string[] = [];
	const fetch = (async (url: string | URL) => {
		urls.push(String(url));
		return handler(String(url));
	}) as unknown as typeof globalThis.fetch;
	return { fetch, urls };
}

describe('loadClientBookPin', () => {
	it('reads the books and the resolution in one round trip', async () => {
		const { fetch, urls } = harness((url) =>
			url.endsWith('/crm/classifications')
				? json([BOOK])
				: json({ classification: { ...BOOK, source: 'pinned' } })
		);
		const pin = await loadClientBookPin('con_1', {}, fetch, { canListBooks: true });
		expect(urls).toEqual([
			expect.stringMatching(/\/crm\/classifications$/),
			expect.stringMatching(/\/crm\/contacts\/con_1\/classification$/)
		]);
		expect(pin).toEqual({
			classifications: [BOOK],
			resolvedClassification: { ...BOOK, source: 'pinned' }
		});
	});

	it('does not list the books for a caller who may not read contacts', async () => {
		const { fetch, urls } = harness(() => json({ classification: null }));
		const pin = await loadClientBookPin('con_1', {}, fetch, { canListBooks: false });
		expect(urls).toEqual([expect.stringMatching(/\/classification$/)]);
		expect(pin).toEqual({ classifications: [], resolvedClassification: null });
	});

	it('reads a failure on either call as an empty pin, never a failed record', async () => {
		const { fetch } = harness(() => new Response('down', { status: 503 }));
		await expect(loadClientBookPin('con_1', {}, fetch, { canListBooks: true })).resolves.toEqual({
			classifications: [],
			resolvedClassification: null
		});
	});

	it('refuses a list that is not a list', async () => {
		const { fetch } = harness((url) =>
			url.endsWith('/crm/classifications')
				? json({ detail: 'odd' })
				: json({ classification: null })
		);
		const pin = await loadClientBookPin('con_1', {}, fetch, { canListBooks: true });
		expect(pin.classifications).toEqual([]);
	});
});
