import { fail, redirect } from '@sveltejs/kit';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation,
	targetRequest
} from '$lib/operate';
import type { TermItem, TermVersion } from '$lib/terms-catalogue';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [item, versions] = await Promise.all([
		targetJson<TermItem>(fetch, locals.accessToken, tenant.uid, `/terms/${params.termUid}`),
		targetJson<TermVersion[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/terms/${params.termUid}/versions`
		)
	]);
	return {
		tenantUid: tenant.uid,
		item: item.ok ? item.data : null,
		versions: versions.ok ? versions.data : [],
		canManage: writing,
		loadError: !item.ok
	};
};

type ActionEvent = Parameters<NonNullable<Actions['save']>>[0];

async function mutate(e: ActionEvent, path: string, method: string, body?: unknown) {
	if (!canOperateWrite(e.locals)) return readOnlyFailure();
	const response = await targetMutation(
		e.fetch,
		e.locals.accessToken,
		e.params.uid,
		`/terms/${e.params.termUid}${path}`,
		{ method, body: body === undefined ? undefined : JSON.stringify(body) }
	);
	if (!response.ok) return mutationFailure(response, 'Could not update terms.');
	return { ok: true };
}

export const actions: Actions = {
	save: async (e) => {
		const f = await e.request.formData();
		return mutate(e, '', 'PATCH', {
			title: String(f.get('title') ?? '').trim(),
			audience: 'client',
			required_at: f.getAll('required_at').map(String).filter(Boolean),
			body_kind: String(f.get('body_kind') ?? 'html'),
			body_md: String(f.get('body_md') ?? ''),
			applicability: {
				always: f.get('always') === 'on',
				products: f.getAll('products').map(String).filter(Boolean),
				has_transfer: f.get('has_transfer') === 'on'
			}
		});
	},
	publish: (e) => mutate(e, '/publish', 'POST'),
	archive: async (e) => {
		const result = await mutate(e, '/archive', 'POST');
		if (result && 'ok' in result) redirect(303, `/tenants/${e.params.uid}/operate/terms`);
		return result;
	},
	upload: async (e) => {
		if (!canOperateWrite(e.locals)) return readOnlyFailure();
		const file = (await e.request.formData()).get('file');
		if (!(file instanceof File) || !file.size) return fail(400, { error: 'Choose a PDF.' });
		const body = new FormData();
		body.set('file', file);
		const response = await targetRequest(
			e.fetch,
			e.locals.accessToken,
			e.params.uid,
			`/terms/${e.params.termUid}/file`,
			{ method: 'POST', body }
		);
		if (!response.ok) return mutationFailure(response, 'Could not upload the PDF.');
		return { ok: true };
	}
};
