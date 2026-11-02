import { error, fail, redirect } from '@sveltejs/kit';
import type { ActionEvent } from '@graphene/ui';
import { toRow, type ApiDocument } from '@graphene/ui/book/documents';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [document, activity] = await Promise.all([
		targetJson<ApiDocument>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/documents/${params.documentUid}`
		),
		targetJson<{ items: ActionEvent[] }>(
			fetch,
			locals.accessToken,
			tenant.uid,
			'/firm/audit/actions',
			{ document: params.documentUid, limit: 25 }
		)
	]);
	if (!document.ok) {
		if (document.status === 404) error(404, 'Document not found.');
		error(document.status || 500, 'Could not load the document.');
	}
	return {
		doc: toRow(document.data),
		events: activity.ok ? activity.data.items : [],
		loadError: !activity.ok,
		previewUrl: document.data.content_type
			? `/tenants/${tenant.uid}/operate/documents/${params.documentUid}/content`
			: null,
		previewContentType: document.data.content_type,
		writing
	};
};

async function mutate(
	event: Parameters<NonNullable<Actions[keyof Actions]>>[0],
	path: string,
	method: string,
	body?: unknown
) {
	if (!canOperateWrite(event.locals)) return readOnlyFailure();
	const response = await targetMutation(
		event.fetch,
		event.locals.accessToken,
		event.params.uid,
		path,
		{
			method,
			body: body === undefined ? undefined : JSON.stringify(body)
		}
	);
	if (!response.ok) return fail(response.status, { error: await detail(response) });
	return { ok: true };
}

export const actions: Actions = {
	rename: async (event) => {
		const name = String((await event.request.formData()).get('name') ?? '').trim();
		if (!name) return fail(400, { error: 'A document needs a name.' });
		return mutate(event, `/documents/${event.params.documentUid}`, 'PATCH', { name });
	},
	retry: (event) => mutate(event, `/documents/${event.params.documentUid}/retry`, 'POST'),
	archive: (event) => mutate(event, `/documents/${event.params.documentUid}/archive`, 'POST'),
	restore: (event) => mutate(event, `/documents/${event.params.documentUid}/unarchive`, 'POST')
};
