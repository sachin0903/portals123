/**
 * The email-template send-test proxy. Sending a firm's own email to your own inbox spends that firm's
 * sending reputation, so this door is the target tenant's WRITE scope — and the target decides which
 * family that is (`model:*` for the preset, `tenants:*` for a firm). See `../../proxy-harness.ts`.
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

const SENT = {
	status: 'delivered',
	to: 'ops@graphene.test',
	subject: '[TEST] Welcome',
	detail: null,
	missing: []
};
const CANVAS = { subject: 's', preheader: 'p', html: '<p>x</p>', css: '' };

/* eslint-disable @typescript-eslint/no-explicit-any */
const runPost = (args: object) => (POST as any)(args);
/* eslint-enable @typescript-eslint/no-explicit-any */

const send = (
	tenant: { id: number; uid: string },
	principal: ReturnType<typeof locals>,
	body: unknown = CANVAS,
	respond: () => Response = () => okJson(SENT)
) => {
	const api = tenantApi(tenant, respond);
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

describe('sending a test of a firm’s template', () => {
	it('forwards the live canvas to the uid-addressed endpoint', async () => {
		const c = send(FIRM, locals(['tenants:write']));
		expect(await (await c.run()).json()).toMatchObject({ status: 'delivered' });
		expect(c.made()).toEqual([
			`POST ${API}/admin/tenants/ten_firm/email-templates/tpl_1/send-test`
		]);
		expect(JSON.stringify(c.calls[0].headers)).toContain('tok');
		expect(c.sent()).toEqual({ ...CANVAS, audience: 'client' });
	});

	it('never forwards a recipient, however the browser dresses one up', async () => {
		const c = send(FIRM, locals(['tenants:write']), { ...CANVAS, to: 'client@example.com' });
		await c.run();
		// The exact key set — nothing that could aim this at somebody else.
		expect(Object.keys(c.sent()).sort()).toEqual([
			'audience',
			'css',
			'html',
			'preheader',
			'subject'
		]);
		expect(JSON.stringify(c.sent())).not.toContain('client@example.com');
	});

	it('forwards the staff audience, so a task notice is tested in the platform brand', async () => {
		// The handler builds its body key by key, so dropping `audience` leaves the API's `"client"`
		// default the only value that can reach it — and a task notice really sends with
		// `portal="platform"` (`app/tasks/notifier.py`), so the MODEL preset's task notice would be
		// tested in the wrong brand from the wrong sender identity. On this door that is the only inbox
		// test the preset can have at all: the model tenant has no logins.
		const c = send(FIRM, locals(['tenants:write']), { ...CANVAS, audience: 'staff' });
		await c.run();
		expect(c.sent().audience).toBe('staff');
	});

	it.each([undefined, 'nonsense', 7])(
		'normalises %p to the client audience rather than letting the API 422',
		async (audience) => {
			const c = send(FIRM, locals(['tenants:write']), { ...CANVAS, audience });
			await c.run();
			expect(c.sent().audience).toBe('client');
		}
	);

	it('passes a 429 back as a response carrying Retry-After (an error() could not)', async () => {
		const c = send(FIRM, locals(['tenants:write']), CANVAS, () =>
			okJson({ detail: 'Too many test emails.' }, 429, { 'retry-after': '30' })
		);
		const res = await c.run();
		expect(res.status).toBe(429);
		expect(res.headers.get('retry-after')).toBe('30');
		expect(await res.json()).toEqual({ message: 'Too many test emails.' });
	});

	it('passes other API failures through with their status', async () => {
		const c = send(FIRM, locals(['tenants:write']), CANVAS, () =>
			okJson({ detail: 'Email template not found' }, 404)
		);
		await expect(c.run()).rejects.toMatchObject({ status: 404 });
	});
});

describe('the write gate is the TARGET tenant’s', () => {
	for (const [why, tenant, principal, status] of [
		[
			'a read-only operator may preview a firm’s email but not send it',
			FIRM,
			locals(['tenants:read']),
			403
		],
		['an anonymous request is refused before resolving anything', FIRM, anonymous(), 401],
		[
			'tenants:write on the model tenant needs model:write instead',
			MODEL,
			locals(['tenants:write']),
			403
		]
	] as const) {
		it(`refuses: ${why}`, async () => {
			const c = send(tenant, principal);
			await expect(c.run()).rejects.toMatchObject({ status });
			expect(c.calls).toEqual([]);
		});
	}

	it('admits model:write on the model tenant', async () => {
		const c = send(MODEL, locals(['model:write']));
		await c.run();
		expect(c.made()).toEqual([
			`POST ${API}/admin/tenants/ten_model/email-templates/tpl_1/send-test`
		]);
	});
});
