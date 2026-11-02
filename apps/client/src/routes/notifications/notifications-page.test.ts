import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type LoadArg = Parameters<typeof load>[0];

describe('notifications during onboarding', () => {
	it('returns the client to onboarding instead of showing the feed', async () => {
		await expect(
			load({
				locals: { accessToken: 'token' },
				parent: async () => ({ activeContactId: 7, onboarding: true }),
				url: new URL('https://portal.test/notifications'),
				fetch: async () => {
					throw new Error('the notification feed must not be fetched');
				}
			} as unknown as LoadArg)
		).rejects.toMatchObject({ status: 303, location: '/onboarding' });
	});
});

describe('the client archive', () => {
	it('sends the ticked filters and the ACTIVE CONTACT together', async () => {
		// A login can be linked to more than one client, so a filter that lost `contact_id` would
		// quietly show another client's notifications.
		const asked: string[] = [];
		const fetch = vi.fn(async (url: string) => {
			asked.push(url);
			return new Response('{"items":[],"has_more":false}', {
				headers: { 'Content-Type': 'application/json' }
			});
		});
		const query = '?category=documents&category=messages&state=unread&state=bogus';
		await load({
			locals: { accessToken: 'token' },
			parent: async () => ({ activeContactId: 7, onboarding: false }),
			url: new URL(`https://portal.test/notifications${query}`),
			fetch
		} as unknown as LoadArg);
		const params = new URLSearchParams(String(asked[0]).split('?')[1]);
		expect(params.get('contact_id')).toBe('7');
		expect(params.getAll('category')).toEqual(['documents', 'messages']);
		expect(params.getAll('state')).toEqual(['unread']);
	});
});

describe('the client archive when the API cannot answer', () => {
	it('reports a loadError rather than an empty inbox', async () => {
		// An archive that reads "nothing here" while the API is refusing hides exactly the outage
		// notices it exists to keep. 403 is non-transient, so there is no retry to wait out.
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const fetch = vi.fn(async () => new Response('refused', { status: 403 }));
		const result = await load({
			locals: { accessToken: 'token' },
			parent: async () => ({ activeContactId: null, onboarding: false }),
			url: new URL('https://portal.test/notifications'),
			fetch
		} as unknown as LoadArg);
		expect(result).toMatchObject({ notifications: [], loadError: true });
	});
});
