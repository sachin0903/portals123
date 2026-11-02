/**
 * The cross-tenant directory page's loader.
 *
 * `GET /admin/users` changed from a bare array to a `{users, total, limit, offset}` envelope with
 * server-side `q`/`limit`/`offset` — a breaking response shape, and this page is its only consumer.
 * Pinned from the consumer end so the two halves cannot drift: if the API were reverted to an array,
 * or the query params dropped, these fail rather than the page quietly rendering an empty list and a
 * total of zero. (`packages/api-client` is still generated against the old array shape — flagged for
 * a deliberate regeneration; no TS consumer calls it, which is why nothing type-errors today.)
 *
 * Also covers the two states the audit called out as previously indistinguishable: a 403 and an
 * unreachable API must surface an error, never an empty directory.
 */

import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { load } from './+page.server';

type Locals = {
	user: unknown;
	accessToken: string | null;
	permissions: string[];
	role: string | null;
};

const OPS: Locals = {
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions: ['tenants:read'],
	role: 'reviewer'
};

const ROW = {
	user_id: '9d4dcb59-0000-4000-8000-000000000000',
	user_uid: 'usr_4b1e77',
	email: 'p@firm.test',
	name: 'P Person',
	enabled: true,
	workos_user_id: 'user_p',
	tenant_id: 4,
	tenant_name: 'Firm',
	tenant_alias: 'firm',
	memberships: 1
};

function stubFetch(body: unknown, status = 200) {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: RequestInfo | URL) => {
		calls.push(String(input));
		return { ok: status < 400, status, json: async () => body } as unknown as Response;
	});
	return { fetch, calls };
}

// The loader only uses `url`, `locals` and `fetch`.
const invoke = (
	args: { url: URL; locals: Locals; fetch: unknown }
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
) => (load as any)(args);

describe('the users directory loader', () => {
	it('asks the API to page and search, and reads the envelope', async () => {
		const { fetch, calls } = stubFetch({ users: [ROW], total: 137, limit: 50, offset: 50 });
		const result = await invoke({
			url: new URL('http://admin.test/users?q=p%40firm&offset=50'),
			locals: OPS,
			fetch
		});
		const requested = new URL(calls[0]);
		expect(requested.pathname).toBe('/admin/users');
		expect(requested.searchParams.get('limit')).toBe(String(PAGE_SIZE));
		expect(requested.searchParams.get('offset')).toBe('50');
		expect(requested.searchParams.get('q')).toBe('p@firm');
		// The envelope, not the array: `total` is the whole directory, `users` just this page.
		expect(result.total).toBe(137);
		expect(result.users).toEqual([ROW]);
		expect(result.apiError).toBeNull();
	});

	it('omits an empty q rather than searching for the empty string', async () => {
		const { fetch, calls } = stubFetch({ users: [], total: 0, limit: 50, offset: 0 });
		await invoke({ url: new URL('http://admin.test/users?q=%20%20'), locals: OPS, fetch });
		expect(new URL(calls[0]).searchParams.has('q')).toBe(false);
	});

	it('surfaces a 403 instead of an empty directory', async () => {
		const { fetch } = stubFetch({ detail: 'Forbidden' }, 403);
		const result = await invoke({ url: new URL('http://admin.test/users'), locals: OPS, fetch });
		expect(result.users).toEqual([]);
		expect(result.apiError).toMatch(/system\/admin role/);
	});

	it('surfaces an unreachable API instead of an empty directory', async () => {
		const fetch = vi.fn(async () => {
			throw new Error('ECONNREFUSED');
		});
		const result = await invoke({ url: new URL('http://admin.test/users'), locals: OPS, fetch });
		expect(result.users).toEqual([]);
		expect(result.apiError).toMatch(/unavailable/i);
	});
});
