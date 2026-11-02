/** The email-template edit load (default prefill + version) and save/publish actions. See
 *  `../../harness.ts`. */
import { describe, expect, it } from 'vitest';
import { load, actions } from './+page.server';
import { formRequest, harness, json, staff } from '../../harness';

// load() is typed PageServerLoad, whose return permits `void` (redirect branches) — exclude it so the
// resolved data's properties are accessible in the (never-redirecting) test calls.
type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;

const DEFAULT_CONTENT = {
	subject: 'Welcome {{ portal_label }}',
	preheader: 'Set a password to activate your account.',
	html: '<p>hi</p>',
	css: ''
};

/** GET routing for the load tests; `defaultStatus` 404s the /default endpoint. */
const loadRoutes =
	(defaultStatus = 200) =>
	(u: string) => {
		if (u.endsWith('/default'))
			return defaultStatus === 200
				? json(DEFAULT_CONTENT)
				: json({ detail: 'no default' }, defaultStatus);
		if (u.includes('/versions/')) return json({ uid: 'v1', subject: 'S', schema: { html: 'x' } });
		if (u.endsWith('/versions')) return json([{ uid: 'v1', status: 'draft', version_number: 1 }]);
		return json({ uid: 'eml_1', code: 'invite', name: 'Invitation', category: 'transactional' });
	};

const runLoad = (defaultStatus = 200) =>
	load({
		params: { uid: 'eml_1' },
		locals: staff,
		fetch: harness(loadRoutes(defaultStatus)).fetch
	} as never) as Promise<LoadData>;

describe('email-template edit load', () => {
	it('loads the template, head version and the built-in default — preheader included', async () => {
		// The preheader is the value the editor fills the inbox-preview box with on "Load default". It is
		// read here as a typed property, so `svelte-check` fails too if the local `ApiEmailDefault`
		// narrows it away: a loader that drops it publishes an override with no preview text.
		const data = await runLoad();
		expect(data.template.code).toBe('invite');
		expect(data.version?.uid).toBe('v1');
		expect(data.canManage).toBe(true);
		expect(data.defaultContent?.subject).toBe('Welcome {{ portal_label }}');
		expect(data.defaultContent?.preheader).toBe('Set a password to activate your account.');
	});

	it('yields defaultContent = null when the code has no built-in default (404)', async () => {
		expect((await runLoad(404)).defaultContent).toBeNull();
	});
});

describe('the save/publish actions', () => {
	it('?/save posts subject/preheader + schema{html,css} and returns the new version', async () => {
		const h = harness((_, init) =>
			init?.method === 'POST' ? json({ uid: 'v2', version_number: 2, status: 'draft' }) : json({})
		);
		const res = await actions.save!({
			params: { uid: 'eml_1' },
			request: formRequest({
				subject: 'Hi {{ first_name }}',
				preheader: 'pre',
				html: '<p>Hi {{ first_name }}</p>',
				css: '.x{color:red}'
			}),
			locals: staff,
			fetch: h.fetch
		} as never);
		expect(res).toMatchObject({ ok: true, vid: 'v2' });
		expect(h.sent()).toMatchObject({
			subject: 'Hi {{ first_name }}',
			preheader: 'pre',
			schema: { html: '<p>Hi {{ first_name }}</p>', css: '.x{color:red}' }
		});
	});

	it('?/publish refuses when no version id was provided', async () => {
		const res = await actions.publish!({
			params: { uid: 'eml_1' },
			request: formRequest({}),
			locals: staff,
			fetch: harness(() => json({})).fetch
		} as never);
		expect(res).toMatchObject({ status: 400 });
	});
});
