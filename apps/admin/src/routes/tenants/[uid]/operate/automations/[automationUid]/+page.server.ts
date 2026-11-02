import { fail, redirect } from '@sveltejs/kit';
import type {
	AutomationRow,
	AutomationSettings,
	DeliveryRow,
	DryRunResult,
	FlowTarget
} from '@graphene/ui/automations';
import { automationBody, malformedAutomation, mutateAutomation } from '$lib/operate-automations';
import { canOperateWrite, readOnlyFailure, targetJson } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent, params, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const query = new URLSearchParams({ limit: '50' });
	const before = url.searchParams.get('before');
	if (before) query.set('before', before);
	const [automation, deliveries, settings, flows] = await Promise.all([
		targetJson<AutomationRow>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/automations/${params.automationUid}`
		),
		targetJson<{ items: DeliveryRow[]; has_more: boolean; next_before: number | null }>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/automations/${params.automationUid}/deliveries`,
			query
		),
		targetJson<AutomationSettings>(fetch, locals.accessToken, tenant.uid, '/automations/settings'),
		targetJson<FlowTarget[]>(fetch, locals.accessToken, tenant.uid, '/automations/flows')
	]);
	if (!automation.ok) redirect(303, `/tenants/${tenant.uid}/operate/automations`);
	return {
		automation: automation.data,
		deliveries: deliveries.ok ? deliveries.data.items : [],
		hasMore: deliveries.ok ? deliveries.data.has_more : false,
		nextBefore: deliveries.ok ? deliveries.data.next_before : null,
		settings: settings.ok ? settings.data : null,
		flows: flows.ok ? flows.data : [],
		canWrite: writing
	};
};

export const actions: Actions = {
	update: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const body = automationBody(await event.request.formData());
		if (!body) return malformedAutomation();
		const response = await mutateAutomation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/automations/${event.params.automationUid}`,
			'PATCH',
			body
		);
		return response instanceof Response ? { updated: true } : response;
	},
	remove: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const response = await mutateAutomation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/automations/${event.params.automationUid}`,
			'DELETE'
		);
		if (!(response instanceof Response)) return response;
		redirect(303, `/tenants/${event.params.uid}/operate/automations`);
	},
	dryRun: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const raw = String((await event.request.formData()).get('subject_id') ?? '').trim();
		const subjectId = raw ? Number(raw) : null;
		if (subjectId !== null && !Number.isInteger(subjectId))
			return fail(400, { error: 'Give a record id.' });
		const response = await mutateAutomation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/automations/${event.params.automationUid}/test`,
			'POST',
			{ subject_id: subjectId }
		);
		return response instanceof Response
			? { dryRun: (await response.json()) as DryRunResult }
			: response;
	}
};
