/**
 * The automations screen's loader and form actions.
 *
 * The page itself is a thin mount of `@graphene/ui/automations`; everything that touches the API
 * lives here, which is the house split (loaders and form actions app-side, components shared).
 *
 * The gates mirror the API's exactly, and they are re-checked here rather than trusted from the
 * component: a form action is a public endpoint as far as the browser is concerned, so
 * "the button wasn't rendered" is not an authorisation control. `automations:pause` is deliberately
 * separate from `automations:write` — an incident responder stops the estate without gaining
 * authoring rights, and a rule author doesn't silently acquire the kill switch.
 */
import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type { AutomationRow, AutomationSettings, FlowTarget } from '@graphene/ui/automations';
import { apiUrl, authHeaders, detail, fetchJson, jsonHeaders } from '$lib/api';
import type { Actions, PageServerLoad } from './$types';

/** What the listing renders — the rules, the estate's settings and the journey picker — in one
 *  streamed object. */
export type AutomationsEstate = {
	automations: AutomationRow[];
	loadFailed: boolean;
	settings: AutomationSettings | null;
	flows: FlowTarget[];
};

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'automations:read')) {
		error(403, 'You do not have permission to view automations.');
	}
	// The gates are SYNCHRONOUS and the estate is STREAMED: the page's frame renders the moment it
	// is navigated to and the rules land behind it, rather than the first byte waiting on three API
	// reads (`clients/+page.server.ts` is the pattern). `redirect`/`error` stay out here — they
	// have to be thrown before the response starts.
	return {
		estate: loadEstate(locals.accessToken, fetch),
		canWrite: can(locals.permissions, locals.role, 'automations:write'),
		canPause: can(locals.permissions, locals.role, 'automations:pause')
	};
};

async function loadEstate(
	accessToken: string | null,
	fetch: typeof globalThis.fetch
): Promise<AutomationsEstate> {
	const h = authHeaders(accessToken);
	// The journey picker and the settings both degrade gracefully: a failure there thins the sheet
	// or hides the pause banner, but it must not take down the listing, which is the page.
	const [automations, settings, flows] = await Promise.all([
		fetchJson<AutomationRow[]>(fetch, `${apiUrl()}/automations`, h),
		fetchJson<AutomationSettings>(fetch, `${apiUrl()}/automations/settings`, h),
		fetchJson<FlowTarget[]>(fetch, `${apiUrl()}/automations/flows`, h)
	]);
	return {
		automations: automations.ok ? automations.data : [],
		loadFailed: !automations.ok,
		settings: settings.ok ? settings.data : null,
		flows: flows.ok ? flows.data : []
	};
}

/**
 * Build the API body from the sheet's form data.
 *
 * The sheet posts the condition tree and the action config as JSON strings (serialised
 * synchronously in its submit handler — never from a rendered hidden input, which is the
 * documented stale-body trap). Parsing failures are a client bug, not a user error, so they refuse
 * with a plain message rather than silently sending `{}` and storing an empty rule.
 */
function automationBody(form: FormData): Record<string, unknown> | { error: string } {
	const name = String(form.get('name') ?? '').trim();
	const triggerKey = String(form.get('trigger_key') ?? '').trim();
	if (!name) return { error: 'An automation needs a name.' };
	if (!triggerKey) return { error: 'Choose a trigger.' };

	let conditions: unknown = null;
	const rawConditions = String(form.get('conditions') ?? '');
	if (rawConditions) {
		try {
			conditions = JSON.parse(rawConditions);
		} catch {
			return { error: 'The conditions could not be read. Reload and try again.' };
		}
	}

	const body: Record<string, unknown> = {
		name,
		trigger_key: triggerKey,
		conditions,
		enabled: form.get('enabled') === '1',
		allow_automation_source: form.get('allow_automation_source') === '1'
	};

	if (String(form.get('target') ?? '') === 'flow') {
		const flowUid = String(form.get('flow_uid') ?? '').trim();
		if (!flowUid) return { error: 'Choose a journey to start.' };
		body.flow_uid = flowUid;
		body.action = null;
	} else {
		const type = String(form.get('action_type') ?? '').trim();
		if (!type) return { error: 'Choose an action.' };
		let config: unknown = {};
		const rawConfig = String(form.get('action_config') ?? '');
		if (rawConfig) {
			try {
				config = JSON.parse(rawConfig);
			} catch {
				return { error: 'The action settings could not be read. Reload and try again.' };
			}
		}
		body.action = { type, config };
		body.flow_uid = null;
	}

	const cron = String(form.get('schedule_cron') ?? '').trim();
	body.schedule = cron ? { cron } : null;

	const rearm = String(form.get('rearm_minutes') ?? '').trim();
	body.rearm_minutes = rearm === '' ? null : Number(rearm);
	return body;
}

/** The API's 422 carries a structured issue list; render all of it, not just the first line. */
async function issueMessage(res: Response): Promise<string> {
	try {
		const body = await res.clone().json();
		const issues = body?.detail?.issues;
		if (Array.isArray(issues) && issues.length) return issues.join(' ');
	} catch {
		/* fall through to the plain detail */
	}
	return detail(res);
}

export const actions: Actions = {
	create: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'automations:write')) {
			return fail(403, { error: 'You do not have permission to create automations.' });
		}
		const body = automationBody(await request.formData());
		if ('error' in body) return fail(400, body);
		const res = await fetch(`${apiUrl()}/automations`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { error: await issueMessage(res) });
		return { created: true };
	},

	update: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'automations:write')) {
			return fail(403, { error: 'You do not have permission to change automations.' });
		}
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No automation specified.' });
		const body = automationBody(form);
		if ('error' in body) return fail(400, body);
		const res = await fetch(`${apiUrl()}/automations/${uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { error: await issueMessage(res) });
		return { updated: true };
	},

	toggle: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'automations:write')) {
			return fail(403, { error: 'You do not have permission to arm automations.' });
		}
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No automation specified.' });
		const enabled = form.get('enabled') === '1';
		const res = await fetch(`${apiUrl()}/automations/${uid}/${enabled ? 'enable' : 'disable'}`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: '{}'
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		// The toast copy is a function of the OUTCOME, so the new state rides back on the result
		// rather than being read from page state frozen at mount (`withToast`'s note).
		return { enabled };
	},

	remove: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'automations:write')) {
			return fail(403, { error: 'You do not have permission to delete automations.' });
		}
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No automation specified.' });
		const res = await fetch(`${apiUrl()}/automations/${uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { removed: true };
	},

	reorder: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'automations:write')) {
			return fail(403, { error: 'You do not have permission to reorder automations.' });
		}
		const form = await request.formData();
		const uids = String(form.get('uids') ?? '')
			.split(',')
			.map((u) => u.trim())
			.filter(Boolean);
		if (!uids.length) return fail(400, { error: 'No order given.' });
		const res = await fetch(`${apiUrl()}/automations/reorder`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ uids })
		});
		if (!res.ok) return fail(res.status, { error: await issueMessage(res) });
		return { reordered: true };
	},

	pause: async ({ request, locals, fetch }) => {
		// The kill switch's own grant — NOT automations:write. See the module comment.
		if (!can(locals.permissions, locals.role, 'automations:pause')) {
			return fail(403, { error: 'You do not have permission to pause automations.' });
		}
		const form = await request.formData();
		const paused = form.get('paused') === '1';
		const reason = String(form.get('paused_reason') ?? '').trim();
		const res = await fetch(`${apiUrl()}/automations/settings`, {
			method: 'PUT',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ paused, paused_reason: paused ? reason || null : null })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { paused };
	}
};
