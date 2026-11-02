/**
 * The admin studio's pages capability (the six splits — docs/api.md): recomposing a tenant's
 * portal PAGES is `pages:write` on top of `tenants:write` (the API's branding PUT diff-gates
 * composition changes on it). The loader threads `canEditPages` into the shared BrandingEditor,
 * whose Pages view renders read-only without it.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const run = (permissions: string[]) =>
	load({
		params: { uid: 'ten_7' },
		parent: async () => ({ tenant: { id: 7, uid: 'ten_7' } }),
		locals: { user: { id: 'ops_1' }, permissions, role: null, accessToken: 'tok' },
		fetch: vi.fn(async () => json({ name: 'Firm', branding: {} }))
	} as never);

describe('the admin branding loader’s pages capability', () => {
	it('tenants:write WITHOUT pages:write — Design stays editable, Pages goes read-only', async () => {
		const data = await run(['tenants:write']);
		expect(data).toMatchObject({ canManage: true, canEditPages: false });
	});

	it('pages:write arms the Pages view', async () => {
		const data = await run(['tenants:write', 'pages:write']);
		expect(data).toMatchObject({ canManage: true, canEditPages: true });
	});

	it('a tenants reader gets neither pen', async () => {
		const data = await run(['tenants:read']);
		expect(data).toMatchObject({ canManage: false, canEditPages: false });
	});
});
