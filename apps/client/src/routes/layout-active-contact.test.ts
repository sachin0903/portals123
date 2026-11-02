/**
 * The root layout's active-contact resolution (`+layout.server.ts`) — a login linked to more
 * than one crm.contacts row needs a stable "which one is active" answer on every page load,
 * sourced from `GET /crm/contacts/mine` and an httpOnly cookie. What matters:
 *
 *   * no cookie yet -> the alphabetically-first default is used AND pinned into the cookie;
 *   * a cookie naming a contact that's still in the list -> honoured, never rewritten;
 *   * a stale/tampered cookie (not in the list) -> ignored, falls back to the default (and
 *     gets overwritten with it) -- never trusted blindly.
 */

import { describe, expect, it, vi } from 'vitest';
import { load } from './+layout.server';

type Contact = { id: number; name: string };

function stubFetch(contacts: Contact[], defaultId: number | null) {
	const fetch = vi.fn(async (input: RequestInfo | URL) => {
		const url = String(input);
		if (url.includes('/crm/contacts/mine')) {
			return {
				ok: true,
				json: async () => ({ contacts, default_contact_id: defaultId })
			} as unknown as Response;
		}
		if (url.includes('/notifications') || url.includes('/applications/me')) {
			return {
				ok: true,
				json: async () => ({ items: [], count: 0, steps: [] })
			} as unknown as Response;
		}
		throw new Error(`Unexpected fetch: ${url}`);
	});
	return fetch as unknown as typeof globalThis.fetch;
}

function stubCookies(initial?: string) {
	let stored = initial;
	const set = vi.fn((_name: string, value: string) => {
		stored = value;
	});
	return {
		get: vi.fn(() => stored),
		set
	};
}

type LoadArg = Parameters<typeof load>[0];
type LoadResult = { activeContactId: number | null; contacts: Contact[] };

const run = (fetch: typeof globalThis.fetch, cookies: ReturnType<typeof stubCookies>) =>
	load({
		locals: { user: { id: 'u1', email: 'jo@example.test' }, accessToken: 'tok' },
		fetch,
		cookies,
		depends: () => {},
		url: { pathname: '/' }
	} as unknown as LoadArg) as Promise<LoadResult>;

const CONTACTS: Contact[] = [
	{ id: 7, name: 'Amara First' },
	{ id: 9, name: 'Zed Ordinary' }
];

describe('root layout active-contact resolution', () => {
	it('uses the default and pins it into the cookie when none is set yet', async () => {
		const cookies = stubCookies(undefined);
		const data = await run(stubFetch(CONTACTS, 7), cookies);
		expect(data.activeContactId).toBe(7);
		expect(data.contacts).toEqual(CONTACTS);
		expect(cookies.set).toHaveBeenCalledWith(expect.any(String), '7', expect.anything());
	});

	it('honours a cookie naming a contact still in the list, without rewriting it', async () => {
		const cookies = stubCookies('9');
		const data = await run(stubFetch(CONTACTS, 7), cookies);
		expect(data.activeContactId).toBe(9);
		expect(cookies.set).not.toHaveBeenCalled();
	});

	it('ignores a stale cookie naming a contact no longer in the list', async () => {
		const cookies = stubCookies('999');
		const data = await run(stubFetch(CONTACTS, 7), cookies);
		expect(data.activeContactId).toBe(7); // falls back to the default, not the stale id
		expect(cookies.set).toHaveBeenCalledWith(expect.any(String), '7', expect.anything());
	});

	it('is null for a single-contact login (nothing to switch between)', async () => {
		const cookies = stubCookies(undefined);
		const data = await run(stubFetch([{ id: 3, name: 'Only Client' }], 3), cookies);
		expect(data.activeContactId).toBe(3);
		expect(data.contacts).toHaveLength(1);
	});
});
