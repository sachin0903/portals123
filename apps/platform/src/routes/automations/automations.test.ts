/**
 * The automations route: its gates, its narrowed-role postures, and the body it builds.
 *
 * Three things are worth a test here, and they are all about lying:
 *
 *  1. **A form action is a public endpoint.** The component hides a control the caller can't use,
 *     but "the button wasn't rendered" is not an authorisation control — every action re-checks its
 *     own gate. `automations:pause` in particular must NOT be reachable with `automations:write`,
 *     and vice versa: the split exists so an incident responder can stop the estate without gaining
 *     authoring rights, and it is worth nothing if either action accepts the other's grant.
 *  2. **The narrowed-role posture reaches the component.** `canWrite`/`canPause` are what the page
 *     turns into the `actions` object, so a read-only principal must load the page and be handed
 *     nothing to post with.
 *  3. **The body must not silently become an empty rule.** The sheet posts the condition tree and
 *     the action config as JSON strings; a parse failure refuses rather than sending `{}`, which
 *     would store an unconditional automation the author never wrote.
 */
import { describe, expect, it, vi } from 'vitest';
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

const row = (over: Record<string, unknown> = {}) => ({
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
	last_detail: null,
	...over
});

const ok: Handler = (url) => {
	if (url.endsWith('/automations')) return json([row()]);
	if (url.endsWith('/automations/settings'))
		return json({
			paused: false,
			paused_reason: null,
			timezone: 'Europe/London',
			effective: {},
			defaults: {},
			ceilings: {},
			max_rules: 200,
			preflight_available: false
		});
	if (url.endsWith('/automations/flows'))
		return json([{ uid: 'flw_1', code: 'onb', name: 'Onboarding' }]);
	return json({}, 404);
};

type LoadArg = Parameters<typeof load>[0];
/** The loader's return type includes `void` (its redirect branch), which none of these cases hit. */
type LoadData = Exclude<Awaited<ReturnType<typeof load>>, void>;
const runLoad = async (event: unknown): Promise<LoadData> =>
	(await load(event as LoadArg)) as LoadData;
type ActionArg = Parameters<typeof actions.create>[0];

function loadEvent(
	fetch: ReturnType<typeof harness>['fetch'],
	permissions: string[] = ['automations:write']
) {
	return {
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch,
		url: new URL('http://portal.test/automations')
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
		request: new Request('http://portal.test/automations', { method: 'POST', body }),
		locals: { user: { id: 'u1' }, permissions, role: null, accessToken: 'tok' },
		fetch
	} as unknown as ActionArg;
}

/** The form the sheet posts for a well-formed rule. */
function sheetForm(over: Partial<Record<string, string>> = {}): FormData {
	const body = new FormData();
	body.set('name', 'Tag new clients');
	body.set('trigger_key', 'contact.created');
	body.set(
		'conditions',
		JSON.stringify({ field: 'contact.type', operator: 'equals', value: 'individual' })
	);
	body.set('target', 'action');
	body.set('action_type', 'apply_tag');
	body.set('action_config', JSON.stringify({ tag: 'new' }));
	body.set('schedule_cron', '');
	body.set('rearm_minutes', '');
	body.set('enabled', '1');
	for (const [k, v] of Object.entries(over)) if (v !== undefined) body.set(k, v);
	return body;
}

// ---- load -----------------------------------------------------------------------------------

describe('load', () => {
	it('refuses without automations:read', async () => {
		const { fetch } = harness(ok);
		await expect(runLoad(loadEvent(fetch, ['tasks:read']))).rejects.toMatchObject({
			status: 403
		});
	});

	it('loads the rules, the settings and the journey picker — streamed behind the first byte', async () => {
		const { fetch, calls } = harness(ok);
		const data = await runLoad(loadEvent(fetch));
		// The estate is a promise the page reads: the gates are answered before the response
		// starts and the three reads land behind it.
		expect(data.estate).toBeInstanceOf(Promise);
		const estate = await data.estate;
		expect(estate.automations).toHaveLength(1);
		expect(estate.settings?.max_rules).toBe(200);
		expect(estate.flows).toHaveLength(1);
		expect(estate.loadFailed).toBe(false);
		// The picker is fetched from the AUTOMATIONS surface, not /flows: somebody arming rules
		// should not need the flow-authoring grant to name a journey.
		expect(calls.some((c) => c.url.endsWith('/automations/flows'))).toBe(true);
	});

	it('READ-ONLY: loads fine and hands the page no write or pause capability', async () => {
		const { fetch } = harness(ok);
		const data = await runLoad(loadEvent(fetch, ['automations:read']));
		expect((await data.estate).automations).toHaveLength(1);
		expect(data.canWrite).toBe(false);
		expect(data.canPause).toBe(false);
	});

	it('PAUSE-ONLY: the incident responder gets the switch and not the pen', async () => {
		const { fetch } = harness(ok);
		const data = await runLoad(loadEvent(fetch, ['automations:read', 'automations:pause']));
		expect(data.canPause).toBe(true);
		expect(data.canWrite).toBe(false);
	});

	it('surfaces a listing failure rather than showing a convincing empty page', async () => {
		const { fetch } = harness((url) =>
			url.endsWith('/automations') ? json({}, 500) : ok(url, undefined)
		);
		const estate = await (await runLoad(loadEvent(fetch))).estate;
		expect(estate.loadFailed).toBe(true);
		expect(estate.automations).toEqual([]);
	});

	it('degrades gracefully when only the side-fetches fail', async () => {
		// A thinner sheet is acceptable; a blank page is not.
		const { fetch } = harness((url) =>
			url.endsWith('/automations') ? json([row()]) : json({}, 500)
		);
		const estate = await (await runLoad(loadEvent(fetch))).estate;
		expect(estate.loadFailed).toBe(false);
		expect(estate.settings).toBeNull();
		expect(estate.flows).toEqual([]);
	});
});

// ---- the gate matrix ------------------------------------------------------------------------

describe('the action gates', () => {
	const write = ['create', 'update', 'toggle', 'remove', 'reorder'] as const;

	it('refuses every write action without automations:write', async () => {
		for (const name of write) {
			const { fetch } = harness(ok);
			const body = sheetForm({ uid: 'aut_1', uids: 'aut_1' });
			const result = await actions[name]!(actionEvent(fetch, body, ['automations:read']));
			expect(result, name).toMatchObject({ status: 403 });
			expect(fetch, name).not.toHaveBeenCalled();
		}
	});

	it('refuses pause without automations:pause — the WRITE grant is not enough', async () => {
		// The load-bearing half of the split: a rule author must not silently acquire the kill switch.
		const { fetch } = harness(ok);
		const body = new FormData();
		body.set('paused', '1');
		const result = await actions.pause!(actionEvent(fetch, body, ['automations:write']));
		expect(result).toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('refuses the write actions with pause alone — the other half of the split', async () => {
		const { fetch } = harness(ok);
		const result = await actions.create!(actionEvent(fetch, sheetForm(), ['automations:pause']));
		expect(result).toMatchObject({ status: 403 });
	});

	it('lets a pause-only principal pause and resume', async () => {
		const { fetch, calls } = harness(() => json({ paused: true }));
		const body = new FormData();
		body.set('paused', '1');
		body.set('paused_reason', 'Investigating a loop');
		const result = await actions.pause!(actionEvent(fetch, body, ['automations:pause']));
		expect(result).toEqual({ paused: true });
		expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({
			paused: true,
			paused_reason: 'Investigating a loop'
		});
	});

	it('drops the reason when resuming — a stale reason on a live estate reads as still paused', async () => {
		const { fetch, calls } = harness(() => json({}));
		const body = new FormData();
		body.set('paused', '');
		body.set('paused_reason', 'Investigating a loop');
		await actions.pause!(actionEvent(fetch, body, ['automations:pause']));
		expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({
			paused: false,
			paused_reason: null
		});
	});
});

// ---- the body the sheet posts -----------------------------------------------------------------

describe('create', () => {
	it('sends the parsed condition tree and action, not the raw strings', async () => {
		const { fetch, calls } = harness(() => json(row(), 201));
		const result = await actions.create!(actionEvent(fetch, sheetForm()));
		expect(result).toEqual({ created: true });
		const body = JSON.parse(String(calls[0]?.init?.body));
		expect(body).toMatchObject({
			name: 'Tag new clients',
			trigger_key: 'contact.created',
			conditions: { field: 'contact.type', operator: 'equals', value: 'individual' },
			action: { type: 'apply_tag', config: { tag: 'new' } },
			flow_uid: null,
			schedule: null,
			rearm_minutes: null,
			enabled: true
		});
	});

	it('sends a flow target with no action, and clears the other side', async () => {
		const { fetch, calls } = harness(() => json(row(), 201));
		const body = sheetForm({ target: 'flow', flow_uid: 'flw_1', action_type: '' });
		await actions.create!(actionEvent(fetch, body));
		const sent = JSON.parse(String(calls[0]?.init?.body));
		expect(sent.flow_uid).toBe('flw_1');
		expect(sent.action).toBeNull();
	});

	it('sends null conditions for an unconditional rule', async () => {
		const { fetch, calls } = harness(() => json(row(), 201));
		await actions.create!(actionEvent(fetch, sheetForm({ conditions: '' })));
		expect(JSON.parse(String(calls[0]?.init?.body)).conditions).toBeNull();
	});

	it('sends a schedule only when one was given', async () => {
		const { fetch, calls } = harness(() => json(row(), 201));
		const body = sheetForm({ trigger_key: 'schedule.recurring', schedule_cron: '0 9 * * 1-5' });
		await actions.create!(actionEvent(fetch, body));
		expect(JSON.parse(String(calls[0]?.init?.body)).schedule).toEqual({ cron: '0 9 * * 1-5' });
	});

	it('REFUSES rather than sending an empty rule when the payload cannot be read', async () => {
		// The failure this guards: swallowing the parse error and posting `{}` would arm an
		// UNCONDITIONAL automation in place of the conditional one the author wrote.
		const { fetch } = harness(() => json(row(), 201));
		const result = await actions.create!(actionEvent(fetch, sheetForm({ conditions: 'not json' })));
		expect(result).toMatchObject({ status: 400 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('refuses a nameless rule and a targetless one before touching the API', async () => {
		for (const over of [{ name: '' }, { target: 'flow', flow_uid: '' }, { action_type: '' }]) {
			const { fetch } = harness(() => json(row(), 201));
			const result = await actions.create!(actionEvent(fetch, sheetForm(over)));
			expect(result, JSON.stringify(over)).toMatchObject({ status: 400 });
			expect(fetch).not.toHaveBeenCalled();
		}
	});

	it('renders the API’s WHOLE issue list, not just the first line', async () => {
		const { fetch } = harness(() =>
			json({ detail: { message: 'Not ready', issues: ['Bad field.', 'Bad test.'] } }, 422)
		);
		const result = await actions.create!(actionEvent(fetch, sheetForm()));
		expect(result).toMatchObject({ status: 422, data: { error: 'Bad field. Bad test.' } });
	});
});

describe('toggle', () => {
	it('calls enable/disable and reports the NEW state back for the toast', async () => {
		// `use:enhance` evaluates its argument once at mount, so a message built from page state is
		// frozen at the state before the first click. The outcome has to ride back on the result.
		const { fetch, calls } = harness(() => json(row()));
		const body = new FormData();
		body.set('uid', 'aut_1');
		body.set('enabled', '1');
		const result = await actions.toggle!(actionEvent(fetch, body));
		expect(result).toEqual({ enabled: true });
		expect(calls[0]?.url).toContain('/automations/aut_1/enable');

		const off = new FormData();
		off.set('uid', 'aut_1');
		off.set('enabled', '');
		const second = harness(() => json(row()));
		const disabled = await actions.toggle!(actionEvent(second.fetch, off));
		expect(disabled).toEqual({ enabled: false });
		expect(second.calls[0]?.url).toContain('/automations/aut_1/disable');
	});
});

describe('reorder', () => {
	it('posts the full order as a list', async () => {
		const { fetch, calls } = harness(() => json([row()]));
		const body = new FormData();
		body.set('uids', 'aut_2, aut_1 ,aut_3');
		const result = await actions.reorder!(actionEvent(fetch, body));
		expect(result).toEqual({ reordered: true });
		expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({
			uids: ['aut_2', 'aut_1', 'aut_3']
		});
	});

	it('refuses an empty order', async () => {
		const { fetch } = harness(() => json([]));
		const body = new FormData();
		body.set('uids', '');
		expect(await actions.reorder!(actionEvent(fetch, body))).toMatchObject({ status: 400 });
		expect(fetch).not.toHaveBeenCalled();
	});
});
