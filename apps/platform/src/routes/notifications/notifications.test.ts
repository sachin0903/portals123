// The archive's round trip: the toolbar's answer into the URL, and the URL into the API query.
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';
import { notificationParams } from './+page.svelte';

type LoadEvent = Parameters<typeof load>[0];

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const event = (query = '', fetch = vi.fn(async () => json({ items: [], has_more: false }))) =>
	({
		url: new URL(`http://portal.test/notifications${query}`),
		locals: { accessToken: 'token' },
		fetch
	}) as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
const run = async (e: LoadEvent) =>
	(await load(e)) as Exclude<Awaited<ReturnType<typeof load>>, void>;
const asked = (e: { fetch: ReturnType<typeof vi.fn> }) =>
	new URLSearchParams(String(e.fetch.mock.calls[0][0]).split('?')[1]);
const NONE = { q: '', category: [], priority: [], state: [], since: '', until: '' };

describe('the /notifications archive', () => {
	it('round-trips every ticked value of one filter, toolbar to API', async () => {
		const written = notificationParams(new URLSearchParams('page=4&focus=note_9'), {
			...NONE,
			state: ['unread', 'read'],
			since: '2026-08-10'
		});
		expect(written.getAll('state')).toEqual(['unread', 'read']);
		expect(written.get('focus')).toBe('note_9');
		expect(written.has('page')).toBe(false);
		const e = event(`?${written.toString()}`);
		await run(e);
		expect(asked(e).getAll('state')).toEqual(['unread', 'read']);
		expect(asked(e).get('since')).toBe('2026-08-10');
		// The archive, not the bell: a dismissed row must not read as one that never happened.
		expect(asked(e).get('include_dismissed')).toBe('true');
	});

	it('takes a cleared filter out of the URL, every occurrence of it', () => {
		const base = new URLSearchParams('category=tasks&category=documents&until=2026-08-24');
		const written = notificationParams(base, { ...NONE, state: ['unread'] });
		expect(written.has('category')).toBe(false);
		expect(written.has('until')).toBe(false);
		expect(written.getAll('state')).toEqual(['unread']);
	});

	it('drops junk values and junk dates, and still reports the filters on a failed fetch', async () => {
		// Junk goes per VALUE, not per filter; `since` reaches `CAST(:since AS date)`, so a forwarded
		// one would 5xx. And no rows under no chips reads as data loss, not as a narrowed archive.
		const dead = vi.fn(async () => json({}, 500));
		const e = event('?state=unread&state=bogus&since=last-tuesday', dead);
		const data = await run(e);
		expect(asked(e).getAll('state')).toEqual(['unread']);
		expect(asked(e).has('since')).toBe(false);
		expect(data.notifications).toEqual([]);
		expect(data.filters).toEqual({ state: ['unread'] });
	});
});
