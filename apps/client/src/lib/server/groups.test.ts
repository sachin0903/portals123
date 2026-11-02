import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearGroupCache, fetchMyGroups } from './groups';

afterEach(() => clearGroupCache());

describe('fetchMyGroups', () => {
	it('returns the slugs in the order the API sent, and caches them per contact', async () => {
		const fetch = vi.fn(async () =>
			Response.json({
				groups: [
					{ uid: 'a', name: 'Workplace', slug: 'workplace' },
					{ uid: 'b', name: 'Charity', slug: 'charity' }
				]
			})
		);
		const opts = { apiUrl: 'http://api.test', fetch, now: () => 1_000 };
		expect(await fetchMyGroups('tok', opts)).toEqual(['workplace', 'charity']);
		expect(await fetchMyGroups('tok', opts)).toEqual(['workplace', 'charity']);
		expect(fetch).toHaveBeenCalledTimes(1);

		await fetchMyGroups('tok', { ...opts, contactId: 92 });
		expect(fetch).toHaveBeenCalledTimes(2);
		expect(fetch).toHaveBeenLastCalledWith(
			'http://api.test/crm/groups/mine?contact_id=92',
			expect.objectContaining({ headers: { Authorization: 'Bearer tok' } })
		);
	});

	it('does not cache a failed read', async () => {
		const down = vi.fn(async () => new Response('no', { status: 503 }));
		const opts = { apiUrl: 'http://api.test', fetch: down, now: () => 1_000 };
		expect(await fetchMyGroups('tok', opts)).toEqual([]);
		expect(await fetchMyGroups('tok', opts)).toEqual([]);
		expect(down).toHaveBeenCalledTimes(2);
	});
});
