/**
 * The tenant-admin section layout's read gate is scope-aware: a general operator takes the firm path
 * (tenants:read → read the firm at its uid), but a MODEL-ONLY operator (model:read/write, no
 * tenants:read) editing the model preset is admitted too — served from /admin/model (model:read
 * gated), since that read is tenants:read gated and would 403 them before scope.ts's model:* branch runs.
 * This is the least-privilege path the email-template surface adds; the layout must not defeat it.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+layout.server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

const MODEL = {
	id: 9,
	uid: 'ten_model',
	alias: 'model',
	name: 'Model Tenant',
	website: null,
	enabled: true,
	parent_id: null,
	workos_project_id: null
};
const FIRM = {
	id: 7,
	uid: 'ten_7',
	alias: 'acme',
	name: 'Acme',
	website: null,
	enabled: true,
	parent_id: null,
	workos_project_id: null
};

// A fetch that stands in for the API: the uid-addressed read returns the firm, /admin/model the model.
// (The API's own permission gates are tested server-side; here we exercise the LAYOUT's routing.)
const api = (branding: Response | Error = json({}, 404)) =>
	vi.fn(async (url: string) => {
		if (url.includes('/branding')) {
			if (branding instanceof Error) throw branding;
			return branding;
		}
		return url.includes('/admin/model')
			? json(MODEL)
			: url.includes('/tenants/ten_7')
				? json(FIRM)
				: json({}, 404);
	});

const run = (uid: string, permissions: string[], fetch = api()) =>
	load({
		params: { uid },
		locals: { user: { id: 'ops_1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	} as never);

describe('the tenant-admin layout read gate', () => {
	it('a tenants reader takes the firm path and gets canManage off tenants:write', async () => {
		const fetch = api();
		const data = (await run('ten_7', ['tenants:read'], fetch)) as {
			tenant: unknown;
			canManage: boolean;
		};
		expect(data.tenant).toMatchObject({ uid: 'ten_7' });
		expect(data.canManage).toBe(false);
		expect(fetch.mock.calls[0][0] as string).toContain('/tenants/ten_7');
	});

	it('a model-only operator on the model tenant is admitted via /admin/model', async () => {
		const fetch = api();
		// model:write implies model:read; deliberately NO tenants:read.
		const data = (await run('ten_model', ['model:write'], fetch)) as {
			tenant: { uid: string };
			canManage: boolean;
		};
		expect(data.tenant).toMatchObject({ uid: 'ten_model', id: 9 });
		expect(data.canManage).toBe(true);
		expect(fetch.mock.calls[0][0] as string).toContain('/admin/model');
	});

	it('a model-only operator on a NON-model uid is refused', async () => {
		await expect(run('ten_7', ['model:read'])).rejects.toMatchObject({ status: 403 });
	});

	it('an operator with neither tenants:read nor model:read is refused', async () => {
		await expect(run('ten_model', [])).rejects.toMatchObject({ status: 403 });
	});
});

describe('the firm icon for the scope header', () => {
	const icon = (data: unknown) =>
		run('ten_7', ['tenants:read'], api(json(data))) as Promise<{ tenantIconUrl: string | null }>;

	it('reads the PLATFORM portal branding alongside the record', async () => {
		const fetch = api(json({ name: 'Acme', branding: { icon_url: 'https://cdn/acme.png' } }));
		const data = (await run('ten_7', ['tenants:read'], fetch)) as { tenantIconUrl: string | null };
		expect(data.tenantIconUrl).toBe('https://cdn/acme.png');
		const urls = fetch.mock.calls.map(([url]) => url as string);
		expect(urls.some((u) => u.endsWith('/tenants/ten_7/branding?portal=platform'))).toBe(true);
		// The record read is still the first call — the tests above index it.
		expect(urls[0]).toContain('/tenants/ten_7');
	});

	it('is null for a firm with no icon set, and never blocks the page', async () => {
		expect((await icon({ name: 'Acme', branding: { icon_url: null } })).tenantIconUrl).toBeNull();
		expect((await icon({ name: 'Acme', branding: {} })).tenantIconUrl).toBeNull();
		expect((await icon({ name: 'Acme', branding: { icon_url: '  ' } })).tenantIconUrl).toBeNull();
		// A failed or unreachable branding read is a missing picture, not a missing page.
		const failing = (await run('ten_7', ['tenants:read'], api(json({}, 500)))) as {
			tenantIconUrl: string | null;
		};
		expect(failing.tenantIconUrl).toBeNull();
		const unreachable = (await run('ten_7', ['tenants:read'], api(new Error('ECONNREFUSED')))) as {
			tenantIconUrl: string | null;
		};
		expect(unreachable.tenantIconUrl).toBeNull();
	});

	it('is not asked for by an operate-only principal, whose branding read would 403', async () => {
		const fetch = api();
		const data = (await run('ten_7', ['tenant_portal:read'], fetch)) as {
			tenantIconUrl: string | null;
		};
		expect(data.tenantIconUrl).toBeNull();
		expect(fetch.mock.calls.some(([url]) => (url as string).includes('/branding'))).toBe(false);
	});
});
