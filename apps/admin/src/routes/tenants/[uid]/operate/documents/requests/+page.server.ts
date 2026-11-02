import { fail, redirect } from '@sveltejs/kit';
import type { ApiRequest, ClientOption } from '@graphene/ui/book/documents';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [requests, clients] = await Promise.all([
		targetJson<ApiRequest[]>(fetch, locals.accessToken, tenant.uid, '/documents/requests'),
		writing
			? targetJson<ClientOption[]>(fetch, locals.accessToken, tenant.uid, '/documents/clients')
			: null
	]);
	return {
		requests: requests.ok ? requests.data : [],
		clients: clients?.ok ? clients.data : [],
		loadError: !requests.ok
	};
};

export const actions: Actions = {
	create: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const contactId = Number(form.get('contact_id'));
		const title = String(form.get('title') ?? '').trim();
		if (!Number.isInteger(contactId) || !title)
			return fail(400, { error: 'Choose a client and describe what is needed.' });
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			'/documents/requests',
			{
				method: 'POST',
				body: JSON.stringify({
					contact_id: contactId,
					priority: String(form.get('priority') ?? 'normal'),
					message: String(form.get('message') ?? '').trim() || null,
					due_date: String(form.get('due_date') ?? '') || null,
					items: [{ action: 'upload', title }]
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	},
	cancel: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const requestUid = String((await event.request.formData()).get('uid') ?? '');
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/documents/requests/${requestUid}/cancel`,
			{ method: 'POST' }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	}
};
