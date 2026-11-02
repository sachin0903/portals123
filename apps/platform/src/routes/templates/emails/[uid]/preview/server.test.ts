/** The preview proxy: the read gate, and the body it forwards server-side. It NEVER sends — it only
 *  relays to the API's render-only /preview endpoint. See `../../harness.ts`. */
import { describe, expect, it } from 'vitest';
import { POST } from './+server';
import { anonymous, harness, json, jsonRequest, withPermissions } from '../../harness';

const reader = withPermissions(['email_templates:read']);
const CANVAS = { subject: 'Hi {{ first_name }}', preheader: '', html: '<p>x</p>', css: '' };

const preview = (
	locals: App.Locals,
	body: unknown = CANVAS,
	routes: (url: string) => Response = () => json({})
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

describe('preview proxy POST', () => {
	it('forwards the editor content and returns the rendered preview', async () => {
		const c = preview(reader, CANVAS, (u) =>
			u.endsWith('/preview')
				? json({ subject: 'Hi Sarah', preheader: '', html: '<!DOCTYPE html>…', missing: [] })
				: json({}, 404)
		);
		const res = await c.run();
		expect(res.status).toBe(200);
		expect((await res.json()).subject).toBe('Hi Sarah');
		expect(c.sent()).toMatchObject({ subject: 'Hi {{ first_name }}', html: '<p>x</p>' });
	});

	it.each([
		['staff', 'staff'],
		['client', 'client'],
		[undefined, 'client'],
		['nonsense', 'client']
	])('forwards audience %p as %p', async (given, expected) => {
		// `audience` chooses which of the firm's two brands the render is dressed in. The handler builds
		// its body key by key, so dropping the field leaves the API's `"client"` default the only
		// reachable value — and an author previewing `task_assigned`, which really sends with
		// `portal="platform"`, sees the wrong brand.
		const c = preview(reader, { ...CANVAS, audience: given });
		await c.run();
		expect(c.sent().audience).toBe(expected);
	});

	it('passes the API error status through', async () => {
		await expect(
			preview(reader, {}, () => json({ detail: 'template gone' }, 404)).run()
		).rejects.toMatchObject({ status: 404 });
	});

	for (const [why, locals, status] of [
		['an anonymous request', anonymous, 401],
		['a user without email_templates:read', withPermissions([]), 403]
	] as const) {
		it(`refuses ${why}`, async () => {
			await expect(preview(locals, {}).run()).rejects.toMatchObject({ status });
		});
	}
});
