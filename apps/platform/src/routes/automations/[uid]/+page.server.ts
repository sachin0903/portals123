/**
 * One automation: the rule, its run log, and the dry run.
 *
 * The write actions are re-declared here rather than shared with the listing route because
 * SvelteKit form actions are per-route; they are thin wrappers over the same endpoints and the same
 * gates. The one thing this route owns is `dryRun` — the safety feature — which is gated on
 * `automations:write` to match the API (it is an authoring affordance that reads a subject's facts,
 * not part of the read grant's listing-and-log job).
 */
import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type {
	AutomationRow,
	AutomationSettings,
	DeliveryRow,
	DryRunResult,
	FlowTarget
} from '@graphene/ui/automations';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail, fetchJson, jsonHeaders } from '$lib/api';
import type { Actions, PageServerLoad } from './$types';

/** The run log's page size — the shared listing default, so it pages like every other listing. */
const PAGE = PAGE_SIZE;

export const load: PageServerLoad = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'automations:read')) {
		error(403, 'You do not have permission to view automations.');
	}
	const h = authHeaders(locals.accessToken);
	// The run log's keyset cursor rides the URL so a deep page survives a reload and can be shared.
	const before = url.searchParams.get('before') ?? '';
	const query = new URLSearchParams({ limit: String(PAGE) });
	if (before) query.set('before', before);

	const [automation, deliveries, settings, flows] = await Promise.all([
		fetchJson<AutomationRow>(fetch, `${apiUrl()}/automations/${params.uid}`, h),
		fetchJson<{ items: DeliveryRow[]; has_more: boolean; next_before: number | null }>(
			fetch,
			`${apiUrl()}/automations/${params.uid}/deliveries?${query}`,
			h
		),
		fetchJson<AutomationSettings>(fetch, `${apiUrl()}/automations/settings`, h),
		fetchJson<FlowTarget[]>(fetch, `${apiUrl()}/automations/flows`, h)
	]);
	if (!automation.ok) {
		error(automation.status === 404 ? 404 : automation.status, 'Automation not found.');
	}
	return {
		automation: automation.data,
		deliveries: deliveries.ok ? deliveries.data.items : [],
		hasMore: deliveries.ok ? deliveries.data.has_more : false,
		nextBefore: deliveries.ok ? deliveries.data.next_before : null,
		settings: settings.ok ? settings.data : null,
		flows: flows.ok ? flows.data : [],
		canWrite: can(locals.permissions, locals.role, 'automations:write')
	};
};

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

async function issueMessage(res: Response): Promise<string> {
	try {
		const body = await res.clone().json();
		const issues = body?.detail?.issues;
		if (Array.isArray(issues) && issues.length) return issues.join(' ');
	} catch {
		/* fall through */
	}
	return detail(res);
}

export const actions: Actions = {
	update: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'automations:write')) {
			return fail(403, { error: 'You do not have permission to change automations.' });
		}
		const body = automationBody(await request.formData());
		if ('error' in body) return fail(400, body);
		const res = await fetch(`${apiUrl()}/automations/${params.uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { error: await issueMessage(res) });
		return { updated: true };
	},

	remove: async ({ params, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'automations:write')) {
			return fail(403, { error: 'You do not have permission to delete automations.' });
		}
		const res = await fetch(`${apiUrl()}/automations/${params.uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		// The record is gone, so staying on its page would 404 on the next load.
		redirect(303, '/automations');
	},

	/**
	 * THE DRY RUN. Reports what the automation would do against one subject and performs nothing —
	 * the API endpoint has no path to a dispatch target at all (pinned by its own test). This action
	 * only relays the verdict back to the page.
	 */
	dryRun: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'automations:write')) {
			return fail(403, { error: 'You do not have permission to test automations.' });
		}
		const form = await request.formData();
		const raw = String(form.get('subject_id') ?? '').trim();
		const subjectId = raw === '' ? null : Number(raw);
		if (subjectId !== null && !Number.isInteger(subjectId)) {
			return fail(400, { error: 'Give a record id to test against.' });
		}
		const res = await fetch(`${apiUrl()}/automations/${params.uid}/test`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ subject_id: subjectId })
		});
		if (!res.ok) return fail(res.status, { error: await issueMessage(res) });
		return { dryRun: (await res.json()) as DryRunResult };
	}
};
