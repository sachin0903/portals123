import { fail } from '@sveltejs/kit';
import { mutationFailure, targetMutation } from '$lib/operate';

export function automationBody(form: FormData): Record<string, unknown> | null {
	const name = String(form.get('name') ?? '').trim();
	const triggerKey = String(form.get('trigger_key') ?? '').trim();
	if (!name || !triggerKey) return null;
	let conditions: unknown = null;
	let config: unknown = {};
	try {
		const raw = String(form.get('conditions') ?? '');
		if (raw) conditions = JSON.parse(raw);
		const actionConfig = String(form.get('action_config') ?? '');
		if (actionConfig) config = JSON.parse(actionConfig);
	} catch {
		return null;
	}
	const body: Record<string, unknown> = {
		name,
		trigger_key: triggerKey,
		conditions,
		enabled: form.get('enabled') === '1',
		allow_automation_source: form.get('allow_automation_source') === '1'
	};
	if (String(form.get('target') ?? '') === 'flow') {
		body.flow_uid = String(form.get('flow_uid') ?? '');
		body.action = null;
	} else {
		body.flow_uid = null;
		body.action = { type: String(form.get('action_type') ?? ''), config };
	}
	const cron = String(form.get('schedule_cron') ?? '').trim();
	body.schedule = cron ? { cron } : null;
	const rearm = String(form.get('rearm_minutes') ?? '').trim();
	body.rearm_minutes = rearm ? Number(rearm) : null;
	return body;
}

export async function mutateAutomation(
	fetch: typeof globalThis.fetch,
	token: string | null,
	tenantUid: string,
	path: string,
	method: 'POST' | 'PATCH' | 'PUT' | 'DELETE',
	body?: unknown
) {
	const response = await targetMutation(fetch, token, tenantUid, path, {
		method,
		...(body === undefined ? {} : { body: JSON.stringify(body) })
	});
	if (!response.ok) return mutationFailure(response, 'Could not update the automation.');
	return response;
}

export const malformedAutomation = () =>
	fail(400, { error: 'The automation settings could not be read.' });
