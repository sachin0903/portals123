/**
 * The single-automation route — and, above everything else on it, the DRY RUN.
 *
 * The dry run is the feature that makes a rule authorable with confidence, and its value is
 * entirely conditional on being trustworthy. The API endpoint has no code path to a dispatch target
 * (pinned there by `tests/test_automations_api.py`); what this file pins is the app side of that
 * promise — the action is gated, it POSTs to `/test` and nothing else, and it relays the verdict
 * rather than inventing one.
 */
import { describe, expect, it, vi } from 'vitest';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { actions, load } from './+page.server';

type Handler = (url: string, init: RequestInit | undefined) => Response | Promise<Response>;

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

function harness(handler: Handler) {
	const calls: { url: string; init: RequestInit | undefined }[] = [];
	const fetch = vi.fn(async (input: unknown, init?: RequestInit) => {
		const url = String(input);
		calls.push({ url, init });
		return handler(url, init);
	});
	return { fetch, calls };
}

const automation = {
	uid: 'aut_1',
	name: 'Chase',
	trigger_key: 'contact.created',
	trigger_label: 'A client record is created',
	trigger_source: 'action',
	subject_kind: 'contact',
	supported: true,
	conditions: null,
	action: { type: 'apply_tag', config: { tag: 'new' } },
	config: {},
	priority: 100,
	enabled: true,
	rearm_minutes: null,
	schedule: null,
	allow_automation_source: false,
	flow_uid: null,
	flow_code: null,
	flow_name: null,
	flow_status: null,
	last_fired_at: null,
	last_outcome: null,
	last_detail: null
};

const verdict = {
	performed: false,
	would_fire: false,
	matched: false,
	blockers: ['This automation is switched off.'],
	subject: { kind: 'contact', id: 7, found: true },
	trigger: { key: 'contact.created', label: 'A client record is created', source: 'action' },
	target: { kind: 'action' },
	explanation: { kind: 'always', passed: true },
	facts: {}
};

const ok: Handler = (url) => {
	if (url.includes('/deliveries')) return json({ items: [], has_more: false, next_before: null });
	if (url.endsWith('/automations/settings')) return json({ paused: false, max_rules: 200 });
	if (url.endsWith('/automations/flows')) return json([]);
	return json(automation);
};

type LoadArg = Parameters<typeof load>[0];
/** The loader's return type includes `void` (its redirect branch), which none of these cases hit. */
type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;
const runLoad = async (event: unknown): Promise<LoadData> =>
	(await load(event as LoadArg)) as LoadData;
type ActionArg = Parameters<typeof actions.update>[0];

function loadEvent(
	fetch: ReturnType<typeof harness>['fetch'],
	{ permissions = ['automations:write'], before = '' } = {}
) {
	return {
		params: { uid: 'aut_1' },
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch,
		url: new URL(`http://portal.test/automations/aut_1${before ? `?before=${before}` : ''}`)
	};
}

function actionEvent(
	fetch: ReturnType<typeof harness>['fetch'],
	body: FormData,
	permissions: string[] = ['automations:write']
): ActionArg {
	// Cast at the SOURCE: a hand-built event is deliberately a subset of SvelteKit's
	// RequestEvent (no cookies, no platform…), and none of these handlers touch the rest.
	return {
		params: { uid: 'aut_1' },
		request: new Request('http://portal.test/automations/aut_1', { method: 'POST', body }),
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	} as unknown as ActionArg;
}

describe('load', () => {
	it('refuses without automations:read', async () => {
		const { fetch } = harness(ok);
		await expect(runLoad(loadEvent(fetch, { permissions: ['tasks:read'] }))).rejects.toMatchObject({
			status: 403
		});
	});

	it('loads the rule and its run log', async () => {
		const { fetch, calls } = harness(ok);
		const data = await runLoad(loadEvent(fetch));
		expect(data.automation.uid).toBe('aut_1');
		expect(data.deliveries).toEqual([]);
		expect(calls.some((c) => c.url.includes(`/deliveries?limit=${PAGE_SIZE}`))).toBe(true);
	});

	it('carries the run log’s keyset cursor from the URL, so a deep page survives a reload', async () => {
		const { fetch, calls } = harness(ok);
		await runLoad(loadEvent(fetch, { before: '42' }));
		expect(calls.some((c) => c.url.includes('before=42'))).toBe(true);
	});

	it('404s a missing (or another firm’s) automation', async () => {
		const { fetch } = harness((url) => (url.includes('/deliveries') ? json({}) : json({}, 404)));
		await expect(runLoad(loadEvent(fetch))).rejects.toMatchObject({ status: 404 });
	});

	it('READ-ONLY: loads, but the page is told it cannot write', async () => {
		const { fetch } = harness(ok);
		const data = await runLoad(loadEvent(fetch, { permissions: ['automations:read'] }));
		expect(data.canWrite).toBe(false);
	});
});

describe('the dry run', () => {
	it('is gated, and touches nothing when refused', async () => {
		const { fetch } = harness(() => json(verdict));
		const body = new FormData();
		body.set('subject_id', '7');
		const result = await actions.dryRun!(actionEvent(fetch, body, ['automations:read']));
		expect(result).toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('POSTs to /test and NOTHING else — no enable, no update, no side effect', async () => {
		const { fetch, calls } = harness(() => json(verdict));
		const body = new FormData();
		body.set('subject_id', '7');
		await actions.dryRun!(actionEvent(fetch, body));
		expect(calls).toHaveLength(1);
		expect(calls[0]?.url).toMatch(/\/automations\/aut_1\/test$/);
		expect(calls[0]?.init?.method).toBe('POST');
		expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({ subject_id: 7 });
	});

	it('relays the server’s verdict verbatim — there is one evaluator and it is the API’s', async () => {
		const { fetch } = harness(() => json(verdict));
		const body = new FormData();
		body.set('subject_id', '7');
		const result = await actions.dryRun!(actionEvent(fetch, body));
		expect(result).toEqual({ dryRun: verdict });
		expect((result as { dryRun: typeof verdict }).dryRun.performed).toBe(false);
	});

	it('sends a null subject rather than NaN when the field is left blank', async () => {
		const { fetch, calls } = harness(() => json(verdict));
		const body = new FormData();
		body.set('subject_id', '');
		await actions.dryRun!(actionEvent(fetch, body));
		expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({ subject_id: null });
	});

	it('refuses a non-numeric subject before calling the API', async () => {
		const { fetch } = harness(() => json(verdict));
		const body = new FormData();
		body.set('subject_id', 'the Vale family');
		const result = await actions.dryRun!(actionEvent(fetch, body));
		expect(result).toMatchObject({ status: 400 });
		expect(fetch).not.toHaveBeenCalled();
	});
});

describe('write actions', () => {
	it('are gated on automations:write', async () => {
		for (const name of ['update', 'remove'] as const) {
			const { fetch } = harness(() => json(automation));
			const body = new FormData();
			body.set('name', 'x');
			body.set('trigger_key', 'contact.created');
			body.set('target', 'action');
			body.set('action_type', 'apply_tag');
			const result = await actions[name]!(actionEvent(fetch, body, ['automations:read']));
			expect(result, name).toMatchObject({ status: 403 });
			expect(fetch, name).not.toHaveBeenCalled();
		}
	});

	it('delete leaves the record’s own page rather than 404ing on the next load', async () => {
		const { fetch } = harness(() => new Response(null, { status: 204 }));
		await expect(actions.remove!(actionEvent(fetch, new FormData()))).rejects.toMatchObject({
			status: 303,
			location: '/automations'
		});
	});
});
