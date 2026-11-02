import { fail, redirect } from '@sveltejs/kit';
import type { ClientOption } from '@graphene/ui/book/documents';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const clients = await targetJson<ClientOption[]>(
		fetch,
		locals.accessToken,
		tenant.uid,
		'/documents/clients'
	);
	return {
		documentUid: url.searchParams.get('document') ?? '',
		clients: clients.ok ? clients.data : [],
		loadError: !clients.ok
	};
};

export const actions: Actions = {
	default: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const documentUid = String(form.get('document_uid') ?? '').trim();
		const contactIds = form.getAll('contact_ids').map(Number).filter(Number.isInteger);
		if (!documentUid || !contactIds.length)
			return fail(400, { error: 'Choose a document and at least one recipient.' });
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/documents/${documentUid}/send`,
			{
				method: 'POST',
				body: JSON.stringify({
					contact_ids: contactIds,
					message: String(form.get('message') ?? '').trim() || null
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true, result: await response.json() };
	}
};
