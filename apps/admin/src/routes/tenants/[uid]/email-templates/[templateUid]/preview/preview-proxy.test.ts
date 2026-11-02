/**
 * The email-template preview proxy. Read-only and render-only — it never sends — so the gate is the
 * target tenant's READ scope, and the target decides the family. See `../../proxy-harness.ts`.
 *
 * `audience` must survive the forward. It chooses which of the firm's two brands dresses the render — a
 * send-time fact the template does not carry, which is why the API takes it as a field
 * (`PreviewEmail.audience`). The handler builds its body key by key, so dropping the field leaves the
 * API's `"client"` default the only value that can arrive, and an operator curating `task_assigned` —
 * which really sends with `portal="platform"` (`app/tasks/notifier.py`) — previews the wrong brand.
 */
import { describe, expect, it } from 'vitest';
import { POST } from './+server';
import {
	anonymous,
	API,
	FIRM,
	jsonRequest,
	locals,
	MODEL,
	okJson,
	tenantApi
} from '../../proxy-harness';

const RENDERED = { subject: 'Hi Sarah', preheader: '', html: '<!DOCTYPE html>…', missing: [] };
const CANVAS = { subject: 's', preheader: 'p', html: '<p>x</p>', css: '' };

/* eslint-disable @typescript-eslint/no-explicit-any */
const runPost = (args: object) => (POST as any)(args);
/* eslint-enable @typescript-eslint/no-explicit-any */

const preview = (
	tenant: { id: number; uid: string },
	principal: ReturnType<typeof locals>,
	body: unknown = CANVAS,
	status = 200
) => {
	const api = tenantApi(tenant, () =>
		status === 200 ? okJson(RENDERED) : okJson({ detail: 'nope' }, status)
	);
	return {
		...api,
		run: () =>
			runPost({
				params: { uid: tenant.uid, templateUid: 'tpl_1' },
				request: jsonRequest(body),
				locals: principal,
				fetch: api.fetch
			})
	};
};

describe('previewing a tenant’s template', () => {
	it('forwards the live canvas to the uid-addressed endpoint, defaulting to the client brand', async () => {
		const c = preview(FIRM, locals(['tenants:read']));
		expect(await (await c.run()).json()).toMatchObject({ subject: 'Hi Sarah' });
		expect(c.made()).toEqual([`POST ${API}/admin/tenants/ten_firm/email-templates/tpl_1/preview`]);
		expect(c.sent()).toEqual({ ...CANVAS, audience: 'client' });
	});

	it.each([
		['staff', 'staff'],
		['client', 'client'],
		[undefined, 'client'],
		['nonsense', 'client']
	])('forwards audience %p as %p', async (given, expected) => {
		const c = preview(FIRM, locals(['tenants:read']), { ...CANVAS, audience: given });
		await c.run();
		expect(c.sent().audience).toBe(expected);
	});

	it('passes an API failure through with its status', async () => {
		await expect(preview(FIRM, locals(['tenants:read']), CANVAS, 404).run()).rejects.toMatchObject({
			status: 404
		});
	});
});

describe('the read gate is the TARGET tenant’s', () => {
	for (const [why, tenant, principal, status] of [
		['an anonymous request, before resolving anything', FIRM, anonymous(), 401],
		['an operator with neither read scope', FIRM, locals([]), 403],
		[
			'tenants:read on the MODEL preset — that needs model:read',
			MODEL,
			locals(['tenants:read']),
			403
		]
	] as const) {
		it(`refuses ${why}, touching no preview endpoint`, async () => {
			const c = preview(tenant, principal);
			await expect(c.run()).rejects.toMatchObject({ status });
			expect(c.calls).toEqual([]);
		});
	}

	it('admits model:read on the MODEL preset', async () => {
		const c = preview(MODEL, locals(['model:read']));
		await c.run();
		expect(c.made()).toEqual([`POST ${API}/admin/tenants/ten_model/email-templates/tpl_1/preview`]);
	});
});
