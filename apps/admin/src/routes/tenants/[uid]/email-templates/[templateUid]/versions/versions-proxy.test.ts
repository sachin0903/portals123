/**
 * The email-template version-history proxy. Version history is the revert mechanism for a template —
 * there is no "reset to default" — so who may do WHAT through it is the whole question: reading history
 * is the target tenant's READ scope, restoring is its WRITE scope, and the target decides which family
 * that is (`model:*` for the preset, `tenants:*` for a firm). See `../../proxy-harness.ts`.
 */
import { describe, expect, it } from 'vitest';
import { GET, POST } from './+server';
import { API, FIRM, locals, MODEL, okJson, tenantApi } from '../../proxy-harness';

const VERSIONS = [
	{
		uid: 'ver_2',
		version_number: 2,
		status: 'published',
		subject: 'Welcome',
		created_at: '2026-08-01T09:00:00Z',
		created_by: 'ops@graphene.test',
		published_at: '2026-08-01T09:05:00Z',
		published_by: 'ops@graphene.test'
	}
];

/* eslint-disable @typescript-eslint/no-explicit-any */
const runGet = (args: object) => (GET as any)(args);
const runPost = (args: object) => (POST as any)(args);
/* eslint-enable @typescript-eslint/no-explicit-any */

const api = (tenant: { id: number; uid: string }) =>
	tenantApi(tenant, (url) =>
		url.endsWith('/restore') ? okJson({ uid: 'ver_3', status: 'draft' }) : okJson(VERSIONS)
	);

const restoreBody = (body = '{"vid":"ver_2"}') =>
	new Request('http://a/x', { method: 'POST', body });

const args = (
	tenant: { uid: string },
	permissions: readonly string[],
	fetch: typeof globalThis.fetch,
	/** `?vid=` asks for ONE version, content included — how the history dialog shows a version
	 *  rather than only restoring it. */
	vid?: string
) => ({
	params: { uid: tenant.uid, templateUid: 'tpl_1' },
	url: new URL(`http://a/x${vid ? `?vid=${vid}` : ''}`),
	locals: locals(permissions),
	fetch
});

describe('listing a firm template’s versions', () => {
	it('admits tenants:read', async () => {
		const c = api(FIRM);
		const res = await runGet(args(FIRM, ['tenants:read'], c.fetch));
		expect(await res.json()).toEqual(VERSIONS);
		expect(c.made()).toEqual([`GET ${API}/admin/tenants/ten_firm/email-templates/tpl_1/versions`]);
	});

	it('refuses an operator with neither scope, before asking the API for anything', async () => {
		const c = api(FIRM);
		await expect(runGet(args(FIRM, ['book:read'], c.fetch))).rejects.toMatchObject({ status: 403 });
		expect(c.calls).toEqual([]);
	});

	it('reads ONE version when asked, so a version can be looked at and not only restored', async () => {
		// Without this the dialog could only offer Restore, and an author chose off a subject line and
		// a date — on a template that goes to a firm's clients.
		const c = api(FIRM);
		await runGet(args(FIRM, ['tenants:read'], c.fetch, 'ver_2'));
		expect(c.made()).toEqual([
			`GET ${API}/admin/tenants/${FIRM.uid}/email-templates/tpl_1/versions/ver_2`
		]);
	});

	it('encodes the version id rather than pasting it into the path', async () => {
		const c = api(FIRM);
		await runGet(args(FIRM, ['tenants:read'], c.fetch, 'a b/c'));
		expect(c.made()[0]).toContain('/versions/a%20b%2Fc');
	});

	it('still needs the read scope for one version, not just for the list', async () => {
		const c = api(FIRM);
		await expect(runGet(args(FIRM, ['book:read'], c.fetch, 'ver_2'))).rejects.toMatchObject({
			status: 403
		});
		expect(c.calls).toEqual([]);
	});
});

describe('restoring a version', () => {
	it('copies the version into a new draft for a tenants:write operator', async () => {
		const c = api(FIRM);
		const res = await runPost({
			...args(FIRM, ['tenants:write'], c.fetch),
			request: restoreBody()
		});
		expect(await res.json()).toMatchObject({ status: 'draft' });
		expect(c.made()).toEqual([
			`POST ${API}/admin/tenants/ten_firm/email-templates/tpl_1/versions/ver_2/restore`
		]);
	});

	it('admits model:write on the model tenant', async () => {
		const c = api(MODEL);
		await runPost({ ...args(MODEL, ['model:write'], c.fetch), request: restoreBody() });
		expect(c.made()).toEqual([
			`POST ${API}/admin/tenants/ten_model/email-templates/tpl_1/versions/ver_2/restore`
		]);
	});

	for (const [why, tenant, permissions, request, status] of [
		// Restoring needs WRITE: a read-only operator may view history but not revert.
		['a read-only operator', FIRM, ['tenants:read'], restoreBody(), 403],
		// And the model preset is a different permission family.
		['tenants:write on the model tenant', MODEL, ['tenants:write'], restoreBody(), 403],
		// A body with no version must not become a bare restore.
		['a body naming no version', FIRM, ['tenants:write'], restoreBody('{}'), 400]
	] as const) {
		it(`refuses ${why} with a ${status}, touching no versions endpoint`, async () => {
			const c = api(tenant);
			await expect(
				runPost({ ...args(tenant, permissions, c.fetch), request })
			).rejects.toMatchObject({ status });
			expect(c.calls).toEqual([]);
		});
	}
});
