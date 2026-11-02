import { fail, redirect } from '@sveltejs/kit';
import type { ApiConversation, ApiGroup } from '@graphene/ui/book/messages';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [conversations, groups] = await Promise.all([
		targetJson<ApiConversation[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			'/messaging/conversations',
			{ limit: 50 }
		),
		targetJson<ApiGroup[]>(fetch, locals.accessToken, tenant.uid, '/messaging/groups')
	]);
	return {
		conversations: conversations.ok ? conversations.data : [],
		groups: groups.ok ? groups.data : [],
		loadError: !conversations.ok,
		writing
	};
};

function requestItems(form: FormData): unknown[] | null {
	try {
		const items = JSON.parse(String(form.get('items') ?? '[]'));
		return Array.isArray(items) && items.length > 0 ? items : null;
	} catch {
		return null;
	}
}

export const actions: Actions = {
	createRequest: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const form = await request.formData();
		const contactId = Number(form.get('contact_id'));
		const items = requestItems(form);
		if (!Number.isFinite(contactId) || !items) return fail(400, { error: 'Invalid request.' });
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			'/documents/requests',
			{
				method: 'POST',
				body: JSON.stringify({
					contact_id: contactId,
					priority: String(form.get('priority') ?? 'normal'),
					message: String(form.get('message') ?? '').trim() || null,
					due_date: String(form.get('due_date') ?? '') || null,
					items
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: 'Could not create the request.' });
		const created = (await response.json()) as { uid?: string };
		return { ok: true, uid: created.uid };
	},
	createGroupRequest: async ({ request, locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const form = await request.formData();
		const groupUid = String(form.get('group_uid') ?? '');
		const items = requestItems(form);
		if (!groupUid || !items) return fail(400, { error: 'Invalid group request.' });
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			`/messaging/groups/${groupUid}/requests`,
			{
				method: 'POST',
				body: JSON.stringify({
					priority: String(form.get('priority') ?? 'normal'),
					message: String(form.get('message') ?? '').trim() || null,
					due_date: String(form.get('due_date') ?? '') || null,
					items
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: 'Could not create the requests.' });
		return { ok: true };
	}
};
