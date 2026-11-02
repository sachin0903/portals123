/**
 * The illustrations gates (the six splits — docs/api.md): the SECTION is visible to
 * documents:read (filed client paperwork) or illustrations:write (it hosts the generate form),
 * but GENERATING — the composer page and the POST proxy — takes `illustrations:write` alone,
 * split out of documents:write because it calls a paid external engine.
 */
import { describe, expect, it, vi } from 'vitest';
import { actions, load as listingLoad } from './+page.server';
import { load as composerLoad } from './new/+page.server';
import { POST as generateProxy } from './+server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const locals = (permissions: string[], user: unknown = { id: 'u_1' }) => ({
	user,
	permissions,
	role: null,
	accessToken: 'tok'
});

describe('the illustrations listing gate', () => {
	const run = (parentFlags: { canReadDocuments: boolean; canIllustrate: boolean }) =>
		listingLoad({
			locals: locals([]),
			fetch: vi.fn(async () => json([])),
			// The URL names the client by uid; the id the illustrations read filters on comes from
			// the record the layout loaded, which is what `parent()` publishes.
			parent: async () => ({ ...parentFlags, detail: { contact: { id: 42, uid: 'con_9b2e' } } })
		} as never);

	it('refuses a caller with neither documents:read nor illustrations:write', async () => {
		await expect(run({ canReadDocuments: false, canIllustrate: false })).rejects.toMatchObject({
			status: 403
		});
	});

	it('admits either side of the OR — readers see the listing, generators reach their form', async () => {
		for (const flags of [
			{ canReadDocuments: true, canIllustrate: false },
			{ canReadDocuments: false, canIllustrate: true }
		]) {
			const loaded = (await run(flags)) as { library: Promise<{ illustrations: unknown[] }> };
			await expect(loaded.library).resolves.toMatchObject({ illustrations: [] });
		}
	});
});

describe('the composer gate — illustrations:write, not documents:write', () => {
	// The composer loads the firm's models for its picker, so it needs a fetch. The gate must
	// still refuse BEFORE that read — a rejected caller should not cost an API round trip.
	const composer = (permissions: string[], type = 'SIPP') => {
		const fetch = vi.fn(async () => json([{ id: 1, name: 'Balanced', target_return: 5 }]));
		return {
			result: composerLoad({
				locals: locals(permissions),
				fetch,
				url: new URL(`http://localhost/clients/con_9b2e/illustrations/new?type=${type}`),
				params: { uid: 'con_9b2e' }
			} as never),
			fetch
		};
	};
	const run = (permissions: string[]) => composer(permissions);

	it('refuses documents:write alone — yesterday’s key no longer generates', async () => {
		const { result, fetch } = run(['documents:write']);
		await expect(result).rejects.toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('admits illustrations:write, with the models its picker needs', async () => {
		const { result } = run(['illustrations:write']);
		const loaded = (await result) as { models: Promise<unknown[]> };
		await expect(loaded.models).resolves.toEqual([{ id: 1, name: 'Balanced', target_return: 5 }]);
	});

	it('treats an unavailable models read as an empty list, not a failure', async () => {
		// A firm with no models configured cannot illustrate, and the picker says so — that is a
		// better outcome than a 500 on a page whose gate the caller passed.
		const fetch = vi.fn(async () => json({ detail: 'nope' }, 503));
		const loaded = (await composerLoad({
			locals: locals(['illustrations:write']),
			fetch,
			url: new URL('http://localhost/clients/con_9b2e/illustrations/new?type=GIA'),
			params: { uid: 'con_9b2e' }
		} as never)) as { models: Promise<unknown[]> };
		await expect(loaded.models).resolves.toEqual([]);
	});

	it('sends a caller who has not chosen a product type back to the listing', async () => {
		await expect(
			composerLoad({
				locals: locals(['illustrations:write']),
				fetch: vi.fn(),
				url: new URL('http://localhost/clients/con_9b2e/illustrations/new'),
				params: { uid: 'con_9b2e' }
			} as never)
		).rejects.toMatchObject({
			status: 303,
			location: '/clients/con_9b2e/illustrations'
		});
	});
});

describe('the generate POST proxy gate', () => {
	const run = (permissions: string[]) => {
		let forwarded = false;
		// A standalone handler cannot read the record's layout data, so it resolves the uid before
		// forwarding. There is no resolve to separate out any more: the endpoint is addressed by the
		// uid the route carries, so the ONE call this handler makes IS the call to the engine — which
		// is what the "before forwarding anything" refusals below assert never happens.
		const fetch = vi.fn(async () => {
			forwarded = true;
			return json({ uid: 'doc_1' }, 201);
		});
		const response = generateProxy({
			locals: locals(permissions),
			params: { uid: 'con_9b2e' },
			request: new Request('http://portal.test', { method: 'POST', body: '{}' }),
			fetch
		} as never);
		return { response, wasForwarded: () => forwarded };
	};

	it('refuses documents:write alone before forwarding anything to the engine', async () => {
		const { response, wasForwarded } = run(['documents:write']);
		expect((await response).status).toBe(403);
		expect(wasForwarded()).toBe(false);
	});

	it('forwards for illustrations:write', async () => {
		const { response, wasForwarded } = run(['illustrations:write']);
		expect((await response).status).toBe(200);
		expect(wasForwarded()).toBe(true);
	});
});

describe('share — documents:send, one illustration into the client portal', () => {
	const share = (permissions: string[], uid = 'ill_1') => {
		const body = new FormData();
		if (uid) body.set('uid', uid);
		let forwarded = '';
		const fetch = vi.fn(async (url: string) => {
			forwarded = url;
			return json({ shared_with_client: true });
		});
		const result = actions.share({
			locals: locals(permissions),
			params: { uid: 'con_9b2e' },
			request: new Request('http://portal.test', { method: 'POST', body }),
			fetch
		} as never);
		return { result, forwarded: () => forwarded };
	};

	it('refuses illustrations:write alone — generating is not sharing', async () => {
		const { result, forwarded } = share(['illustrations:write']);
		await expect(result).resolves.toMatchObject({ status: 403 });
		expect(forwarded()).toBe('');
	});

	it('posts the illustration uid to the share endpoint', async () => {
		const { result, forwarded } = share(['documents:send']);
		await expect(result).resolves.toMatchObject({ ok: true });
		expect(forwarded()).toContain('/crm/contacts/con_9b2e/illustrations/ill_1/share');
	});
});
