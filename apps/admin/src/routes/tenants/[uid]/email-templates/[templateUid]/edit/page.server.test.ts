/**
 * Where the editor's brand-colour swatches come from on the OPERATIONS surface.
 *
 * The trap: the admin portal deliberately wears the Graphene PRODUCT look and is not tenant-themed, so
 * its own `page.data.branding` is GRAPHENE's palette — deriving swatches from it would offer an operator
 * Graphene's colours to write, as literals, into a firm's client emails. The loader fetches the TARGET
 * tenant's stored branding instead, so these assert on the URL it asks for rather than the shape it
 * returns. The model preset is the second half: NO swatches and no fetch, because every firm clones the
 * preset and a swatch writes a literal.
 *
 * Also covered: what the loader passes through from the shipped default. The response is `as`-cast
 * onto a local interface, so a field the API returns and the interface omits is dropped silently —
 * `preheader` is the one that matters, since a default loaded without it publishes an email with no
 * inbox preview line.
 */
import { describe, expect, it } from 'vitest';
import { load } from './+page.server';
import { API, FIRM, locals, MODEL, okJson, tenantApi } from '../../proxy-harness';

// Named CSS colours, not hexes: `lib/theme-tokens.test.ts` greps the whole admin tree — tests included —
// for colour literals, and branding accepts a bare keyword just as it accepts hex. What this file asserts
// is which tenant the palette came from, not the values (those live with the derivation in pkgs/ui).
const LIGHT = { brand: 'teal', primary: 'teal', foreground: 'midnightblue', card: null };

const DEFAULT_CONTENT = {
	subject: 'Hi',
	preheader: 'Open it with the secure link in this email.',
	html: '<p>hi</p>',
	css: ''
};

/** `brandingStatus` makes the branding record unreadable; `branding` is the stored record. */
function api(
	tenant: { id: number; uid: string },
	opts: { brandingStatus?: number; branding?: unknown } = {}
) {
	return tenantApi(tenant, (url) => {
		if (url.includes('/branding'))
			return opts.brandingStatus && opts.brandingStatus !== 200
				? okJson({ detail: 'nope' }, opts.brandingStatus)
				: okJson({
						name: tenant.uid,
						portal: 'platform',
						branding: opts.branding ?? { colors: { light: LIGHT }, pages: [{ id: 'p1' }] }
					});
		if (url.endsWith('/versions')) return okJson([]);
		if (url.endsWith('/default')) return okJson(DEFAULT_CONTENT);
		return okJson({ uid: 'tpl_1', code: 'invite', name: 'Client invitation' });
	});
}

/* eslint-disable @typescript-eslint/no-explicit-any */
const run = (
	tenant: { id: number; uid: string; name: string },
	permissions: string[],
	fetch: typeof globalThis.fetch
) =>
	(load as any)({
		params: { uid: tenant.uid, templateUid: 'tpl_1' },
		locals: locals(permissions),
		fetch,
		parent: async () => ({ tenant })
	});
/* eslint-enable @typescript-eslint/no-explicit-any */

const brandingCalls = (made: string[]) => made.filter((u) => u.includes('/branding'));

describe('a firm’s templates', () => {
	it('reads the TARGET tenant’s branding by its uid, carrying the palette only', async () => {
		// The route is uid-addressed; the `/admin/tenants/{tenant_id}` contract is integer. The portal asked
		// for is the one the firm's own editor reads off its host-resolved branding, so both surfaces offer
		// identical swatches — and `pages` never reaches the editor, which wants a list of colours.
		const c = api(FIRM);
		const data = await run(FIRM, ['tenants:read'], c.fetch);
		expect(brandingCalls(c.made())).toEqual([
			`GET ${API}/admin/tenants/ten_firm/branding?portal=platform`
		]);
		expect(data.branding).toEqual({ colors: { light: LIGHT } });
		expect(Object.keys(data.branding)).toEqual(['colors']);
	});

	it('degrades to no swatches when the branding record is unreadable or unset', async () => {
		// An unreachable or forbidden branding record must not 500 the editor: the author loses the swatch
		// row, not the ability to edit. An unbranded firm gets null rather than an empty record, so the
		// page's derivation gets the same "nothing to offer" answer either way.
		for (const brandingStatus of [403, 404, 500]) {
			const data = await run(FIRM, ['tenants:read'], api(FIRM, { brandingStatus }).fetch);
			expect(data.branding).toBeNull();
			expect(data.template).toMatchObject({ uid: 'tpl_1' });
		}
		expect(
			(await run(FIRM, ['tenants:read'], api(FIRM, { branding: {} }).fetch)).branding
		).toBeNull();
	});

	it('carries the shipped default’s preheader through, not just its subject', async () => {
		// The inbox preview line the editor fills in alongside the subject. The loader casts the response
		// onto a local `ApiEmailDefault`, so a shape omitting a field the API returns drops it before the
		// page mounts — and a default published with a blank preheader delivers no preview text at all.
		const data = await run(FIRM, ['tenants:read'], api(FIRM).fetch);
		expect(data.defaultContent).toMatchObject({
			subject: 'Hi',
			preheader: 'Open it with the secure link in this email.'
		});
	});
});

describe('the model preset', () => {
	it('offers no swatches, and does not even ask for branding', async () => {
		const c = api(MODEL);
		const data = await run(MODEL, ['model:read'], c.fetch);
		expect(data.isModel).toBe(true);
		expect(data.branding).toBeNull();
		expect(brandingCalls(c.made())).toEqual([]);
	});
});
