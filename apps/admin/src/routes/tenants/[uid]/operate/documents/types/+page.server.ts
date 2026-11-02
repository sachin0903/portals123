import { fail, redirect } from '@sveltejs/kit';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type DocumentType = {
	uid: string;
	kind: 'document' | 'file';
	slug: string;
	label: string;
	archived: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const result = await targetJson<DocumentType[]>(
		fetch,
		locals.accessToken,
		tenant.uid,
		'/documents/types',
		{ include_archived: true }
	);
	return { types: result.ok ? result.data : [], loadError: !result.ok };
};

export const actions: Actions = {
	create: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const slug = String(form.get('slug') ?? '').trim();
		const label = String(form.get('label') ?? '').trim();
		if (!slug || !label) return fail(400, { error: 'A slug and label are required.' });
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			'/documents/types',
			{
				method: 'POST',
				body: JSON.stringify({
					slug,
					label,
					kind: form.get('kind') === 'file' ? 'file' : 'document'
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	},
	update: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const typeUid = String(form.get('uid') ?? '');
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/documents/types/${typeUid}`,
			{
				method: 'PATCH',
				body: JSON.stringify({
					label: String(form.get('label') ?? '').trim(),
					archived: form.get('archived') === 'true'
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { ok: true };
	}
};
