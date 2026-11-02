/**
 * The cross-tenant email-template list loader and its two write actions.
 *
 * `?/update` is one subject. Without it a template's name or code cannot be corrected from this surface
 * once created — and on the MODEL preset that propagates, since a new firm clones the preset's codes
 * verbatim. `?/delete` is the other: it destroys a template and its version history, so what it forwards
 * from a refusal is the whole of what an operator has to act on.
 *
 * A refusal here REDIRECTS (303) rather than failing 403 — a form action on a page the operator may still
 * read belongs back on that page — which is why this file has its own harness rather than extending
 * `../write-gates.test.ts`, whose `expectGated` asserts a `fail(403)` and a zero total fetch count.
 */
import { describe, expect, it } from 'vitest';
import { actions, load } from './+page.server';
import { API, FIRM, formRequest, locals, MODEL, okJson, tenantApi } from './proxy-harness';

const VALID = { uid: 'tpl_1', code: 'invite', name: 'Client invitation' };

/* eslint-disable @typescript-eslint/no-explicit-any */
const runUpdate = (args: object) => (actions.update as any)(args);
const runDelete = (args: object) => (actions.delete as any)(args);
const runLoad = (args: object) => (load as any)(args);
/* eslint-enable @typescript-eslint/no-explicit-any */

const update = (
	tenant: { id: number; uid: string },
	permissions: readonly string[],
	fields: Record<string, string> = VALID,
	status = 200
) => {
	const api = tenantApi(tenant, () =>
		status === 200 ? okJson({ uid: 'tpl_1' }) : okJson({ detail: 'nope' }, status)
	);
	return {
		...api,
		run: () =>
			runUpdate({
				params: { uid: tenant.uid },
				request: formRequest(fields),
				locals: locals(permissions),
				fetch: api.fetch
			})
	};
};

describe('editing a firm’s template metadata', () => {
	it('is exported at all — without it this surface cannot correct a template', () => {
		expect(actions.update).toBeTypeOf('function');
	});

	it('PATCHes the cross-tenant endpoint by the firm’s uid', async () => {
		// The route is uid-addressed; the API's `/admin/tenants/{tenant_id}` contract is integer.
		const c = update(FIRM, ['tenants:write'], {
			...VALID,
			description: 'Carries the set-password link'
		});
		expect(await c.run()).toMatchObject({ action: 'update', ok: true });
		expect(c.made()).toEqual([`PATCH ${API}/admin/tenants/ten_firm/email-templates/tpl_1`]);
		expect(c.sent()).toEqual({
			code: 'invite',
			name: 'Client invitation',
			description: 'Carries the set-password link'
		});
	});

	it('sends an explicit empty string for a cleared description, so it is actually cleared', async () => {
		// `null` is NOT normalising here, it is the defect: the API's update is
		// `COALESCE(:desc, description)`, so a null means "leave it alone" — deleting a description
		// restores the old one and reports success. Only an ABSENT key preserves.
		const c = update(FIRM, ['tenants:write'], { ...VALID, description: '   ' });
		await c.run();
		expect(c.sent().description).toBe('');
		expect(c.sent()).toHaveProperty('description');
	});

	it('drops `category` even when a stale page posts one — the API coalesces', async () => {
		// The settings dialog has no Category control (nothing reads `category` at send time). The trap is
		// the action: posting a defaulted `notification` into `COALESCE(:cat, category)` would reset a
		// transactional template's category on every rename, so it must be dropped, not forwarded.
		const c = update(FIRM, ['tenants:write'], { ...VALID, category: 'notification' });
		await c.run();
		expect(c.sent()).not.toHaveProperty('category');
	});

	it('surfaces a duplicate code (409) for the dialog to show inline', async () => {
		expect(await update(FIRM, ['tenants:write'], VALID, 409).run()).toMatchObject({ status: 409 });
	});
});

describe('?/update validates before it writes', () => {
	for (const [why, fields] of [
		['no template uid', { code: 'invite', name: 'Client invitation' }],
		['no name', { uid: 'tpl_1', code: 'invite' }],
		['no code', { uid: 'tpl_1', name: 'Client invitation' }]
	] as const) {
		it(`refuses ${why} with a 400 and never PATCHes`, async () => {
			const c = update(FIRM, ['tenants:write'], fields);
			expect(await c.run()).toMatchObject({ status: 400 });
			expect(c.calls).toEqual([]);
		});
	}
});

describe('?/update gates on the TARGET tenant’s scope, not a blanket tenants:write', () => {
	// `$lib/email-templates/scope` is the single answer all four surfaces share, so these drive the action
	// through it rather than restating the rule. The two families do not substitute for each other.
	for (const [why, tenant, permissions] of [
		['a read-only operator on a firm', FIRM, ['tenants:read']],
		[
			'tenants:write on the MODEL preset — curating that needs model:write',
			MODEL,
			['tenants:write']
		],
		['model:write on a FIRM', FIRM, ['model:write']]
	] as const) {
		it(`refuses ${why}, touching no template endpoint`, async () => {
			const c = update(tenant, permissions);
			await expect(c.run()).rejects.toMatchObject({ status: 303 });
			expect(c.calls).toEqual([]);
		});
	}

	it('redirects a refusal back to the page the operator may still read', async () => {
		await expect(update(FIRM, ['tenants:read']).run()).rejects.toMatchObject({
			status: 303,
			location: `/tenants/${FIRM.uid}/email-templates`
		});
	});

	it('admits model:write on the MODEL preset', async () => {
		const c = update(MODEL, ['model:write']);
		expect(await c.run()).toMatchObject({ action: 'update', ok: true });
		expect(c.made()).toEqual([`PATCH ${API}/admin/tenants/ten_model/email-templates/tpl_1`]);
	});
});

const remove = (
	tenant: { id: number; uid: string },
	permissions: readonly string[],
	fields: Record<string, string> = { uid: 'tpl_1' },
	respond: () => Response = () => okJson({ uid: 'tpl_1', code: 'invite' })
) => {
	const api = tenantApi(tenant, respond);
	return {
		...api,
		run: () =>
			runDelete({
				params: { uid: tenant.uid },
				request: formRequest(fields),
				locals: locals(permissions),
				fetch: api.fetch
			})
	};
};

describe('deleting a firm’s template', () => {
	it('is exported at all — it is the only destructive action this surface offers', () => {
		expect(actions.delete).toBeTypeOf('function');
	});

	it('has no archive or restore beside it — soft-archiving is gone from the whole contract', () => {
		// A leftover would post to an endpoint the API no longer serves, and the listing's kebab would
		// offer a way back from an irreversible act.
		expect(actions.archive).toBeUndefined();
		expect(actions.unarchive).toBeUndefined();
	});

	it('DELETEs the cross-tenant endpoint by the firm’s uid', async () => {
		// The route is uid-addressed; the API's `/admin/tenants/{tenant_id}` contract is integer.
		const c = remove(FIRM, ['tenants:write']);
		expect(await c.run()).toMatchObject({ action: 'delete', ok: true });
		expect(c.made()).toEqual([`DELETE ${API}/admin/tenants/${FIRM.uid}/email-templates/tpl_1`]);
	});

	it('refuses no template uid with a 400 and never calls the API', async () => {
		const c = remove(FIRM, ['tenants:write'], {});
		expect(await c.run()).toMatchObject({ status: 400 });
		expect(c.calls).toEqual([]);
	});

	it('forwards a 409’s own sentence, which NAMES what to go and fix', async () => {
		// The API refuses where the send would be lost, and its `detail` names the published journey or
		// the code with no shipped default behind it. Flattening that into "could not delete" throws away
		// the only actionable part — the listing toasts `error` verbatim.
		const blocked =
			"This template cannot be deleted yet — the published journey 'Onboarding' sends it at 'Chase the client'.";
		const c = remove(FIRM, ['tenants:write'], { uid: 'tpl_1' }, () =>
			okJson({ detail: blocked }, 409)
		);
		expect(await c.run()).toMatchObject({
			status: 409,
			data: { action: 'delete', error: blocked }
		});
	});

	it('surfaces a 404 — a second delete is not a cheerful success', async () => {
		const c = remove(FIRM, ['tenants:write'], { uid: 'tpl_1' }, () =>
			okJson({ detail: 'Email template not found' }, 404)
		);
		expect(await c.run()).toMatchObject({
			status: 404,
			data: { error: 'Email template not found' }
		});
	});
});

describe('?/delete gates on the TARGET tenant’s scope, not a blanket tenants:write', () => {
	// Same rule as `?/update`, driven through `$lib/email-templates/scope` rather than restated: a
	// permanent delete is the last action that should be allowed to slip a family.
	for (const [why, tenant, permissions] of [
		['a read-only operator on a firm', FIRM, ['tenants:read']],
		[
			'tenants:write on the MODEL preset — curating that needs model:write',
			MODEL,
			['tenants:write']
		],
		['model:write on a FIRM', FIRM, ['model:write']]
	] as const) {
		it(`refuses ${why}, touching no template endpoint`, async () => {
			const c = remove(tenant, permissions);
			await expect(c.run()).rejects.toMatchObject({ status: 303 });
			expect(c.calls).toEqual([]);
		});
	}

	it('admits model:write on the MODEL preset', async () => {
		const c = remove(MODEL, ['model:write']);
		expect(await c.run()).toMatchObject({ action: 'delete', ok: true });
		expect(c.made()).toEqual([`DELETE ${API}/admin/tenants/${MODEL.uid}/email-templates/tpl_1`]);
	});
});

describe('the loader reads ONE list', () => {
	const loadFor = (tenant: { id: number; uid: string }, permissions: readonly string[]) => {
		const api = tenantApi(tenant, (url) => {
			if (url.endsWith('/catalog')) return okJson([{ code: 'invite' }]);
			return okJson([{ uid: 'tpl_1', code: 'invite' }]);
		});
		return {
			...api,
			run: () =>
				runLoad({
					params: { uid: tenant.uid },
					locals: locals(permissions),
					fetch: api.fetch
				})
		};
	};

	it('never asks the API for a status, and offers nothing archived', async () => {
		// `live|archived|all` is gone from the API and there is no archived view to fill, so a second
		// fetch here would be a call for rows that cannot exist.
		const c = loadFor(FIRM, ['tenants:write']);
		const data = await c.run();
		expect(c.made()).toEqual([
			`GET ${API}/admin/tenants/${FIRM.uid}/email-templates`,
			`GET ${API}/admin/tenants/${FIRM.uid}/email-templates/catalog`
		]);
		expect(data.templates).toEqual([{ uid: 'tpl_1', code: 'invite' }]);
		expect(data).not.toHaveProperty('archivedTemplates');
		expect(data).not.toHaveProperty('templateUsage');
	});

	it('reports a load error off the one list it now reads', async () => {
		const api = tenantApi(FIRM, (url) =>
			url.endsWith('/email-templates') ? okJson({ detail: 'nope' }, 500) : okJson([])
		);
		const data = await runLoad({
			params: { uid: FIRM.uid },
			locals: locals(['tenants:write']),
			fetch: api.fetch
		});
		expect(data.loadError).toBe(true);
		expect(data.templates).toEqual([]);
	});
});
