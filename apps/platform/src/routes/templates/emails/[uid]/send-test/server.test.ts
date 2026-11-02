/** The send-test proxy: the WRITE gate, the body it forwards (and the recipient it does not), and the
 *  throttle passed back with Retry-After. See `../../harness.ts`. */
import { describe, expect, it } from 'vitest';
import { POST } from './+server';
import { anonymous, harness, json, jsonRequest, staff, withPermissions } from '../../harness';

const SENT = {
	status: 'delivered',
	to: 'author@firm.test',
	subject: '[TEST] Welcome',
	detail: null,
	missing: []
};
const CANVAS = { subject: 'Hi {{ first_name }}', preheader: 'p', html: '<p>x</p>', css: 'p{}' };

const send = (
	locals: App.Locals,
	body: unknown = CANVAS,
	routes: (url: string) => Response = () => json(SENT)
) => {
	const h = harness(routes);
	return {
		...h,
		run: () =>
			POST({
				params: { uid: 'eml_1' },
				request: jsonRequest(body),
				locals,
				fetch: h.fetch
			} as never)
	};
};

describe('send-test proxy POST', () => {
	it('forwards the live canvas with the access token, and returns the outcome', async () => {
		const c = send(staff, CANVAS, (u) => (u.endsWith('/send-test') ? json(SENT) : json({}, 404)));
		const res = await c.run();
		expect(res.status).toBe(200);
		expect(await res.json()).toMatchObject({ status: 'delivered', to: 'author@firm.test' });
		expect(c.calls[0].url.endsWith('/email-templates/eml_1/send-test')).toBe(true);
		expect(JSON.stringify(c.calls[0].headers)).toContain('tok');
		expect(c.sent()).toEqual({ ...CANVAS, audience: 'client' });
	});

	it('never forwards a recipient, however the browser dresses one up', async () => {
		const c = send(staff, { subject: 's', html: '', css: '', to: 'client@example.com' });
		await c.run();
		expect(Object.keys(c.sent()).sort()).toEqual([
			'audience',
			'css',
			'html',
			'preheader',
			'subject'
		]);
		expect(JSON.stringify(c.sent())).not.toContain('client@example.com');
	});

	it('forwards a staff audience so the platform brand dresses the test', async () => {
		// `audience` decides which of the firm's two brands, and which sender identity, the test send
		// wears. The handler built its body key by key and omitted it, so the API's `"client"` default was
		// the only value that could arrive — and a task notice really sends with `portal="platform"`
		// (`app/tasks/notifier.py`), so testing one arrived in the wrong brand from the wrong sender, in
		// the very white-label seam an inbox test exists to check.
		const c = send(staff, { subject: 's', html: '', css: '', audience: 'staff' });
		await c.run();
		expect(c.sent().audience).toBe('staff');
	});

	it.each([undefined, 'client', 'nonsense', 42, null])(
		'normalises %p to the client audience rather than letting the API 422',
		async (audience) => {
			const c = send(staff, { subject: 's', html: '', css: '', audience });
			await c.run();
			expect(c.sent().audience).toBe('client');
		}
	);

	it('passes a 429 back as a response carrying Retry-After (an error() could not)', async () => {
		const c = send(staff, {}, () =>
			json({ detail: 'Too many test emails.' }, 429, { 'retry-after': '45' })
		);
		const res = await c.run();
		expect(res.status).toBe(429);
		expect(res.headers.get('retry-after')).toBe('45');
		expect(await res.json()).toEqual({ message: 'Too many test emails.' });
	});

	it('passes other API failures through with their status', async () => {
		for (const [status, detail] of [
			[404, 'Email template not found'],
			[422, 'no email address on record']
		] as const)
			await expect(send(staff, {}, () => json({ detail }, status)).run()).rejects.toMatchObject({
				status
			});
	});

	for (const [why, locals, status] of [
		['an anonymous request', anonymous, 401],
		[
			'a read-only viewer — sending needs email_templates:write',
			withPermissions(['email_templates:read']),
			403
		]
	] as const) {
		it(`refuses ${why}, touching no endpoint`, async () => {
			const c = send(locals, {});
			await expect(c.run()).rejects.toMatchObject({ status });
			expect(c.calls).toEqual([]);
		});
	}
});
