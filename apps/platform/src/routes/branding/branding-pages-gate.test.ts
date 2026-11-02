/**
 * The studio's pages capability (the six splits — docs/api.md): composing the client portal's
 * PAGES is `pages:write`, split out of `branding:write`. The loader threads `canEditPages` into
 * the shared BrandingEditor, whose Pages view renders read-only without it — so the flag this
 * test pins is the one the affordance actually keys off.
 */
import { describe, expect, it, vi } from 'vitest';
import { load, type Studio } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const brandingResponse = { name: 'Firm', branding: {} };

type Loaded = {
	authorised: boolean;
	canManage: boolean;
	canEditPages: boolean;
	studio: Promise<Studio> | null;
};

const run = (
	permissions: string[],
	fetch: typeof globalThis.fetch = vi.fn(async () => json(brandingResponse))
) =>
	load({
		locals: { user: { id: 'u_1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	} as never) as Promise<Loaded>;

describe('the branding loader’s pages capability', () => {
	it('branding:write WITHOUT pages:write — Design stays editable, Pages goes read-only', async () => {
		// The narrowed-role posture: the parent slug alone keeps canManage but not canEditPages.
		const data = await run(['branding:write']);
		expect(data).toMatchObject({ authorised: true, canManage: true, canEditPages: false });
	});

	it('pages:write arms the Pages view', async () => {
		const data = await run(['branding:write', 'pages:write']);
		expect(data).toMatchObject({ canManage: true, canEditPages: true });
	});

	it('a branding reader gets neither pen', async () => {
		const data = await run(['branding:read']);
		expect(data).toMatchObject({ authorised: true, canManage: false, canEditPages: false });
	});
});

describe('the branding loader’s streamed studio', () => {
	it('answers the gates before the first byte and streams the studio reads in one batch', async () => {
		const urls: string[] = [];
		const fetch = vi.fn(async (input: string | URL) => {
			const url = String(input);
			urls.push(url);
			if (url.includes('/firm/branding?portal=client'))
				return json({ name: 'Firm', branding: { font: 'Inter' } });
			if (url.includes('/firm/branding?portal=platform')) return json({ name: '', branding: {} });
			if (url.endsWith('/crm/classifications')) return json([{ uid: 'cls_1', name: 'Advised' }]);
			if (url.endsWith('/crm/groups'))
				return json([{ uid: 'grp_1', name: 'Workplace', slug: 'workplace', sort_order: 0 }]);
			if (url.endsWith('/catalog/products')) return json([]);
			return json({}, 404);
		}) as unknown as typeof globalThis.fetch;

		const data = await run(['branding:read'], fetch);
		expect(data.studio).toBeInstanceOf(Promise);
		// Every read was issued before any had answered — the books and the shelf no longer wait
		// for the branding as a second pair.
		expect(urls).toHaveLength(5);
		const studio = await data.studio;
		expect(studio).toMatchObject({
			forbidden: false,
			error: null,
			name: 'Firm',
			client: { font: 'Inter' },
			classifications: [{ uid: 'cls_1', name: 'Advised' }],
			groups: [{ uid: 'grp_1', name: 'Workplace', slug: 'workplace' }],
			productTypesUnavailable: false
		});
	});

	it('does not touch the API for a caller without branding:read', async () => {
		const fetch = vi.fn(async () => json(brandingResponse));
		const data = await run(['contacts:read'], fetch as unknown as typeof globalThis.fetch);
		expect(data).toEqual({
			authorised: false,
			canManage: false,
			canEditPages: false,
			studio: null
		});
		expect(fetch).not.toHaveBeenCalled();
	});

	it('relays the API’s own refusal as forbidden', async () => {
		const fetch = vi.fn(async () => json({}, 403)) as unknown as typeof globalThis.fetch;
		const studio = await (await run(['branding:read'], fetch)).studio;
		expect(studio).toMatchObject({ forbidden: true, error: null });
	});

	it('reports a failed branding read rather than seeding the editor with blanks', async () => {
		const fetch = vi.fn(async (input: string | URL) =>
			String(input).includes('portal=platform')
				? json({ detail: 'branding store unavailable' }, 503)
				: json(brandingResponse)
		) as unknown as typeof globalThis.fetch;
		const studio = await (await run(['branding:read'], fetch)).studio;
		expect(studio).toMatchObject({ forbidden: false, error: expect.any(String), name: '' });
	});

	it('turns a thrown fetch into a stated failure, never a rejected stream', async () => {
		const fetch = vi.fn(async () => {
			throw new TypeError('fetch failed');
		}) as unknown as typeof globalThis.fetch;
		const studio = await (await run(['branding:read'], fetch)).studio;
		expect(studio).toMatchObject({ forbidden: false, error: expect.any(String) });
	});
});
