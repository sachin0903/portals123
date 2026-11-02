/**
 * The tenant storage browser's gate (permission audit M3/G6).
 *
 * The browser lists — and its /view proxy presigns — a firm's CLIENT files, so both moved from
 * `tenants:read` to `book:read`, in lockstep with the API's own re-gated
 * `/admin/tenants/{id}/storage/objects` + `object-url` endpoints: administering a firm must not
 * imply reading its clients' documents. Mirrors the book layout's gate tests.
 */
import { describe, expect, it } from 'vitest';
import { load } from './+page.server';
import { GET as view } from './view/+server';

type GateLocals = {
	user: unknown;
	accessToken: string | null;
	permissions: string[];
	role: string | null;
};
const locals = (permissions: string[]): GateLocals => ({
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions,
	role: null
});

const PAGE = {
	bucket: 'portals-documents',
	prefix: '',
	per_tenant: false,
	folders: [],
	objects: [],
	object_types: [],
	prefix_labels: {},
	next_token: null
};
const okJson = (body: unknown) =>
	new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'content-type': 'application/json' }
	});

/* eslint-disable @typescript-eslint/no-explicit-any */
const runLoad = (args: object) => (load as any)(args);
const runView = (args: object) => (view as any)(args);
/* eslint-enable @typescript-eslint/no-explicit-any */

describe('the storage browser gate', () => {
	it('refuses tenants:read alone — firm admin is not client-file access', async () => {
		await expect(
			runLoad({
				locals: locals(['tenants:read', 'tenants:write']),
				params: { uid: 'ten_4' },
				parent: async () => ({ tenant: { id: 4, uid: 'ten_4' } }),
				url: new URL('http://admin.test/tenants/ten_4/settings/storage'),
				fetch: () => okJson(PAGE)
			})
		).rejects.toMatchObject({ status: 403 });
	});

	it('admits book:read', async () => {
		const result = await runLoad({
			locals: locals(['book:read']),
			params: { uid: 'ten_4' },
			parent: async () => ({ tenant: { id: 4, uid: 'ten_4' } }),
			url: new URL('http://admin.test/tenants/ten_4/settings/storage'),
			fetch: () => okJson(PAGE)
		});
		expect(result.page?.bucket).toBe('portals-documents');
		expect(result.apiError).toBeNull();
	});
});

describe('the per-object view proxy gate', () => {
	it('refuses tenants:read alone before presigning anything', async () => {
		let presigned = false;
		await expect(
			runView({
				locals: locals(['tenants:read']),
				params: { uid: 'ten_4' },
				parent: async () => ({ tenant: { id: 4, uid: 'ten_4' } }),
				url: new URL('http://admin.test/tenants/ten_4/settings/storage/view?key=documents/a.pdf'),
				fetch: () => {
					presigned = true;
					return okJson({ url: 'https://signed.example/obj' });
				}
			})
		).rejects.toMatchObject({ status: 403 });
		expect(presigned).toBe(false);
	});

	it('redirects a book:read operator to the presigned URL', async () => {
		await expect(
			runView({
				locals: locals(['book:read']),
				params: { uid: 'ten_4' },
				parent: async () => ({ tenant: { id: 4, uid: 'ten_4' } }),
				url: new URL('http://admin.test/tenants/ten_4/settings/storage/view?key=documents/a.pdf'),
				fetch: () => okJson({ url: 'https://signed.example/obj' })
			})
		).rejects.toMatchObject({ status: 302, location: 'https://signed.example/obj' });
	});
});
