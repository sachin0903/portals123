/**
 * The EMAIL half of `/templates` — its slice of the shared loader, and its three write actions.
 *
 * Both template kinds share one route, but the loader fetches only the kind in `?kind=` so the
 * Documents tab never waits on emails. These tests use a principal that holds `email_templates:write`
 * and NOT `templates:read`, so the documents fetches are skipped even without `?kind=emails`.
 *
 * See `./emails/harness.ts` (it stays beside the editor + proxy tests that share it).
 */
import { describe, expect, it } from 'vitest';
import { load, actions } from './+page.server';
import { formRequest, harness, json, staff, withPermissions } from './emails/harness';

// load() is typed PageServerLoad, whose return permits `void` (redirect branches) — exclude it so the
// resolved data's properties are accessible in the (never-redirecting) test calls.
type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;

/** Answers the loads the page makes, each from the URL alone. */
const listRoutes =
	(over: (u: string) => Response | null = () => null) =>
	(u: string) =>
		over(u) ??
		(u.endsWith('/catalog')
			? json([{ code: 'invite', label: 'Invitation' }])
			: json([{ uid: 'l1' }]));

/** The loader reads `url` for the search term (`?q=`), so every call needs one. Unsearched
 *  here: the email slice is not what the search narrows — these tests are about the fetches. */
const URL_NO_QUERY = new URL('http://portal.test/templates');

describe('/templates — the email slice', () => {
	it('returns authorised:false (no redirect) for a signed-in user without read', async () => {
		const { fetch } = harness(() => json([]));
		const data = (await load({
			locals: withPermissions([]),
			fetch,
			url: URL_NO_QUERY
		} as never)) as LoadData;
		expect(data.canEmails).toBe(false);
		expect(data.emailTemplates).toEqual([]);
		expect(data.emailCatalog).toEqual([]);
	});

	it('loads the templates + catalogue and reports canManage', async () => {
		const { fetch, calls } = harness(listRoutes());
		const data = (await load({ locals: staff, fetch, url: URL_NO_QUERY } as never)) as LoadData;
		expect(data.canEmails).toBe(true);
		expect(data.canManageEmails).toBe(true);
		expect(data.emailTemplates).toEqual([{ uid: 'l1' }]);
		expect(data.emailCatalog[0].code).toBe('invite');
		expect(data.loadError).toBe(false);
		expect(calls.some((c) => c.url.endsWith('/catalog'))).toBe(true);
	});

	it('asks for one email list and never for an archived one', async () => {
		// An email template has no archived state: it is deleted outright, and the API's list endpoint
		// takes no `status`, so a stale `?status=archived` would be a 422 dressed as an empty tab.
		// Scoped to the EMAIL endpoint — a documents-reading principal legitimately asks for an
		// archived documents list from this same loader.
		const { fetch, calls } = harness(listRoutes());
		await load({ locals: staff, fetch, url: URL_NO_QUERY } as never);
		const emailCalls = calls.filter((c) => c.url.includes('/email-templates'));
		expect(emailCalls.some((c) => c.url.includes('status='))).toBe(false);
		expect(emailCalls.filter((c) => c.url.endsWith('/email-templates')).length).toBe(1);
	});

	it('does not fetch notification rules on first paint', async () => {
		const { fetch, calls } = harness(listRoutes());
		await load({ locals: staff, fetch, url: URL_NO_QUERY } as never);
		expect(calls.some((c) => c.url.includes('/firm/notification-'))).toBe(false);
	});

	it('does not fetch documents when the email slice is on screen', async () => {
		const both = withPermissions(['email_templates:write', 'templates:read']);
		const { fetch, calls } = harness(listRoutes());
		await load({
			locals: both,
			fetch,
			url: new URL('http://portal.test/templates?kind=emails')
		} as never);
		expect(calls.some((c) => c.url.includes('/email-templates'))).toBe(true);
		expect(
			calls.some((c) => /\/templates(\?|$)/.test(c.url) && !c.url.includes('email-templates'))
		).toBe(false);
	});

	it('does not fetch emails on the documents tab', async () => {
		const both = withPermissions(['email_templates:write', 'templates:read']);
		const { fetch, calls } = harness(listRoutes());
		await load({ locals: both, fetch, url: URL_NO_QUERY } as never);
		expect(calls.some((c) => c.url.includes('/email-templates'))).toBe(false);
		expect(calls.some((c) => c.url.includes('/templates'))).toBe(true);
	});

	it('flags loadError when the template list fails, rather than claiming an empty book', async () => {
		const { fetch } = harness(
			listRoutes((u) => (u.endsWith('/email-templates') ? json({}, 403) : null))
		);
		const data = (await load({ locals: staff, fetch, url: URL_NO_QUERY } as never)) as LoadData;
		expect(data.loadError).toBe(true);
	});
});

/** Runs a list action against a harness that answers the write with `status` (or success). */
function action(
	name: 'emailCreate' | 'emailUpdate' | 'emailDelete',
	method: 'POST' | 'PATCH' | 'DELETE',
	fields: Record<string, string>,
	status?: number,
	detail = 'nope'
) {
	const h = harness((_, init) =>
		init?.method === method
			? status
				? json({ detail }, status)
				: json(
						{ uid: fields.uid ?? 'eml_new', name: 'Client invitation' },
						method === 'POST' ? 201 : 200
					)
			: json([])
	);
	return {
		...h,
		run: () =>
			actions[name]!({ request: formRequest(fields), locals: staff, fetch: h.fetch } as never)
	};
}

describe('?/emailCreate', () => {
	it('creates and returns the uid so the page can open the editor', async () => {
		const c = action('emailCreate', 'POST', {
			code: 'invite',
			name: 'Invitation',
			category: 'transactional'
		});
		expect(await c.run()).toMatchObject({ ok: true, uid: 'eml_new' });
		expect(c.sent()).toMatchObject({
			code: 'invite',
			name: 'Invitation',
			category: 'transactional'
		});
	});

	it('rejects a missing code with a 400, and surfaces the API’s conflict', async () => {
		expect(await action('emailCreate', 'POST', { name: 'Invitation' }).run()).toMatchObject({
			status: 400
		});
		expect(
			await action('emailCreate', 'POST', { code: 'invite', name: 'Dup' }, 409).run()
		).toMatchObject({ status: 409 });
	});
});

describe('?/emailUpdate', () => {
	const VALID = { uid: 'eml_1', code: 'invite', name: 'Client invitation' };

	it('PATCHes the template by uid and echoes ok', async () => {
		const c = action('emailUpdate', 'PATCH', {
			...VALID,
			description: 'Carries the set-password link'
		});
		expect(await c.run()).toMatchObject({ ok: true, action: 'update' });
		expect(c.calls.find((x) => x.method === 'PATCH')!.url).toContain('/email-templates/eml_1');
		expect(c.sent('PATCH')).toMatchObject({
			code: 'invite',
			name: 'Client invitation',
			description: 'Carries the set-password link'
		});
	});

	for (const [why, fields] of [
		['uid', { code: 'invite', name: 'Invitation' }],
		['name', { uid: 'eml_1', code: 'invite' }]
	] as const) {
		it(`rejects a missing ${why} with a 400 and never PATCHes`, async () => {
			const c = action('emailUpdate', 'PATCH', fields);
			expect(await c.run()).toMatchObject({ status: 400 });
			expect(c.made('PATCH')).toBe(false);
		});
	}

	it('never sends `category`, even when a stale page posts one', async () => {
		// No surface asks for a category (nothing reads it at send time; the preview's brand choice is an
		// explicit `audience` param). The trap is what the ACTION does with the absent field:
		// `data.get('category') ?? 'notification'` posts a default into `COALESCE(:cat, category)`,
		// resetting a transactional template's category every time an author corrects its name. A
		// hand-crafted POST must not get through that door either.
		for (const fields of [VALID, { ...VALID, category: 'notification' }]) {
			const c = action('emailUpdate', 'PATCH', fields);
			await c.run();
			expect(c.sent('PATCH')).not.toHaveProperty('category');
		}
	});

	it('sends an explicit empty string for a cleared description, so it is actually cleared', async () => {
		// `null` is NOT normalising here, it is the defect: the API's update is
		// `COALESCE(:desc, description)`, so a null reads as "leave it alone" — deleting a description
		// restores the old one and reports success. Only an ABSENT key preserves, which is what the
		// `category` rule above relies on.
		const c = action('emailUpdate', 'PATCH', { ...VALID, description: '   ' });
		await c.run();
		expect(c.sent('PATCH').description).toBe('');
		expect(c.sent('PATCH')).toHaveProperty('description');
	});

	it('surfaces the API error on a duplicate code (409)', async () => {
		expect(await action('emailUpdate', 'PATCH', VALID, 409).run()).toMatchObject({ status: 409 });
	});
});

describe('?/emailDelete', () => {
	it('DELETEs the template by uid and names what went', async () => {
		const c = action('emailDelete', 'DELETE', { uid: 'eml_1' });
		expect(await c.run()).toMatchObject({ action: 'delete', ok: true, name: 'Client invitation' });
		expect(c.calls.find((x) => x.method === 'DELETE')!.url).toContain('/email-templates/eml_1');
	});

	it('rejects a missing uid with a 400 and never DELETEs', async () => {
		const c = action('emailDelete', 'DELETE', {});
		expect(await c.run()).toMatchObject({ status: 400 });
		expect(c.made('DELETE')).toBe(false);
	});

	it('surfaces the API’s 409 verbatim — it names the journey to fix', async () => {
		// The refusal is the only thing that links this template to the journey that sends it; nothing
		// on the page does. A message of our own ("could not delete") leaves the author hunting.
		const why =
			"This template cannot be deleted yet — the published journey 'Onboarding' sends it at 'Chase the client'.";
		expect(await action('emailDelete', 'DELETE', { uid: 'eml_1' }, 409, why).run()).toMatchObject({
			status: 409,
			data: { action: 'delete', error: why }
		});
	});

	it('reports a 404 rather than claiming a second delete succeeded', async () => {
		expect(
			await action('emailDelete', 'DELETE', { uid: 'gone' }, 404, 'Email template not found').run()
		).toMatchObject({ status: 404, data: { error: 'Email template not found' } });
	});
});
