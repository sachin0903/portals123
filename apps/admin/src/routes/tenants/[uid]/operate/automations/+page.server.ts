import { fail, redirect } from '@sveltejs/kit';
import type { AutomationRow, AutomationSettings, FlowTarget } from '@graphene/ui/automations';
import { automationBody, malformedAutomation, mutateAutomation } from '$lib/operate-automations';
import { canOperateWrite, readOnlyFailure, targetJson } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [automations, settings, flows] = await Promise.all([
		targetJson<AutomationRow[]>(fetch, locals.accessToken, tenant.uid, '/automations'),
		targetJson<AutomationSettings>(fetch, locals.accessToken, tenant.uid, '/automations/settings'),
		targetJson<FlowTarget[]>(fetch, locals.accessToken, tenant.uid, '/automations/flows')
	]);
	return {
		automations: automations.ok ? automations.data : [],
		loadFailed: !automations.ok,
		settings: settings.ok ? settings.data : null,
		flows: flows.ok ? flows.data : [],
		canWrite: writing,
		canPause: writing
	};
};

const writable = (locals: App.Locals) => canOperateWrite(locals);
export const actions: Actions = {
	create: async (event) => {
		if (!writable(event.locals)) return readOnlyFailure();
		const body = automationBody(await event.request.formData());
		if (!body) return malformedAutomation();
		const response = await mutateAutomation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			'/automations',
			'POST',
			body
		);
		return response instanceof Response ? { created: true } : response;
	},
	update: async (event) => {
		if (!writable(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const uid = String(form.get('uid') ?? '');
		const body = automationBody(form);
		if (!uid || !body) return malformedAutomation();
		const response = await mutateAutomation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/automations/${uid}`,
			'PATCH',
			body
		);
		return response instanceof Response ? { updated: true } : response;
	},
	toggle: async (event) => {
		if (!writable(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const uid = String(form.get('uid') ?? '');
		const enabled = form.get('enabled') === '1';
		if (!uid) return fail(400, { error: 'No automation specified.' });
		const response = await mutateAutomation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/automations/${uid}/${enabled ? 'enable' : 'disable'}`,
			'POST',
			{}
		);
		return response instanceof Response ? { enabled } : response;
	},
	remove: async (event) => {
		if (!writable(event.locals)) return readOnlyFailure();
		const uid = String((await event.request.formData()).get('uid') ?? '');
		const response = await mutateAutomation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/automations/${uid}`,
			'DELETE'
		);
		return response instanceof Response ? { removed: true } : response;
	},
	reorder: async (event) => {
		if (!writable(event.locals)) return readOnlyFailure();
		const uids = String((await event.request.formData()).get('uids') ?? '')
			.split(',')
			.filter(Boolean);
		const response = await mutateAutomation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			'/automations/reorder',
			'POST',
			{ uids }
		);
		return response instanceof Response ? { reordered: true } : response;
	},
	pause: async (event) => {
		if (!writable(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const paused = form.get('paused') === '1';
		const response = await mutateAutomation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			'/automations/settings',
			'PUT',
			{ paused, paused_reason: paused ? String(form.get('paused_reason') ?? '') || null : null }
		);
		return response instanceof Response ? { paused } : response;
	}
};
