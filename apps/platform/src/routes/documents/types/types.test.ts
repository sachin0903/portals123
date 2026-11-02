/**
 * The document-type registry loader: the permission answers are synchronous, the registry is
 * streamed, and a failed read is reported rather than rendered as an empty taxonomy.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];
type Loaded = Exclude<Awaited<ReturnType<typeof load>>, void>;

const TYPES = [
	{
		id: 1,
		uid: 'typ_1',
		kind: 'document',
		slug: 'suitability_report',
		label: 'Suitability report',
		sort_order: 1,
		archived: false
	},
	{
		id: 2,
		uid: 'typ_2',
		kind: 'file',
		slug: 'passport',
		label: 'Passport',
		sort_order: 1,
		archived: false
	}
];

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function event(over: { permissions?: string[]; fetch?: typeof globalThis.fetch } = {}) {
	const fetch = over.fetch ?? vi.fn(async () => json(TYPES));
	return {
		locals: {
			user: { id: 'user_1' },
			accessToken: 'token',
			permissions: over.permissions ?? ['document_types:read', 'document_types:write'],
			role: 'adviser'
		},
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

const run = async (e: LoadEvent) => (await load(e)) as Loaded;

describe('the /documents/types loader', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		const e = event();
		(e.locals as { user: unknown }).user = null;
		await expect(load(e)).rejects.toMatchObject({ status: 303, location: '/login' });
	});

	it('answers "not authorised" synchronously, and never calls the API', async () => {
		const e = event({ permissions: ['contacts:read'] });
		const data = await run(e);
		expect(data.authorised).toBe(false);
		expect(data.canManage).toBe(false);
		expect(data.registry).toBeNull();
		expect(e.fetch).not.toHaveBeenCalled();
	});

	it('returns before the API answers — the registry is streamed', async () => {
		const e = event({ fetch: vi.fn(() => new Promise<Response>(() => {})) });
		const data = await run(e);
		expect(data.authorised).toBe(true);
		expect(data.canManage).toBe(true);
		expect(data.registry).toBeInstanceOf(Promise);
	});

	it('separates read from write — a reader gets the rows and no controls', async () => {
		const data = await run(event({ permissions: ['document_types:read'] }));
		expect(data.canManage).toBe(false);
		expect((await data.registry!).types).toEqual(TYPES);
	});

	it('asks for archived rows too, so disabled types can be re-enabled', async () => {
		const e = event();
		await run(e);
		expect(String(e.fetch.mock.calls[0][0])).toContain('include_archived=true');
	});

	it('reports a failed read instead of an empty taxonomy — and never rejects', async () => {
		const refused = await run(event({ fetch: vi.fn(async () => json({ detail: 'nope' }, 500)) }));
		await expect(refused.registry!).resolves.toMatchObject({ types: [], error: 'nope' });

		const down = await run(
			event({
				fetch: vi.fn(async () => {
					throw new Error('ECONNREFUSED');
				})
			})
		);
		const result = await down.registry!;
		expect(result.types).toEqual([]);
		expect(result.error).toContain('Could not load');
	});
});
