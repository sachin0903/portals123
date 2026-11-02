import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { CatalogProduct } from '@graphene/ui/book/journeys';
import { productChoices, termDraftFromForm, type TermDraft, type TermItem } from '$lib/terms';
import type { Actions, PageServerLoad } from './$types';

async function saveDraft(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	uid: string,
	body: TermDraft
) {
	return fetch(`${apiUrl()}/terms/${uid}`, {
		method: 'PATCH',
		headers: jsonHeaders(accessToken),
		body: JSON.stringify(body)
	});
}

export const load: PageServerLoad = async ({ locals, fetch, params }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'terms:read')) redirect(303, '/terms');
	const res = await fetchJson<TermItem>(
		fetch,
		`${apiUrl()}/terms/${params.uid}`,
		authHeaders(locals.accessToken)
	);
	if (!res.ok) redirect(303, '/terms');
	const versions = await fetchJson<{ uid: string; version_number: number; published_at: string }[]>(
		fetch,
		`${apiUrl()}/terms/${params.uid}/versions`,
		authHeaders(locals.accessToken)
	);
	const products = await fetchJson<CatalogProduct[]>(
		fetch,
		`${apiUrl()}/catalog/products`,
		authHeaders(locals.accessToken)
	);
	const applicability = res.data.applicability ?? {};
	const selected = Array.isArray(applicability.products)
		? (applicability.products as string[])
		: [];
	return {
		item: res.data,
		versions: versions.ok ? versions.data : [],
		products: productChoices(products.ok ? products.data : [], selected),
		// The breadcrumb's escape hatch: the shell names a dynamic segment from the loaded entity, and
		// `item` is not one of the keys it knows.
		crumb: res.data.title,
		canManage: can(locals.permissions, locals.role, 'terms:write')
	};
};

export const actions: Actions = {
	save: async ({ request, locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'terms:write')) {
			return fail(403, { error: 'You do not have permission to edit terms.' });
		}
		const body = termDraftFromForm(await request.formData());
		if ('error' in body) return fail(400, body);
		const res = await saveDraft(fetch, locals.accessToken, params.uid, body);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true, operation: 'save' as const };
	},
	publish: async ({ request, locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'terms:write')) {
			return fail(403, { error: 'You do not have permission to publish terms.' });
		}
		const body = termDraftFromForm(await request.formData());
		if ('error' in body) return fail(400, body);
		const saved = await saveDraft(fetch, locals.accessToken, params.uid, body);
		if (!saved.ok) return fail(saved.status, { error: await detail(saved) });

		const res = await fetch(`${apiUrl()}/terms/${params.uid}/publish`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true, operation: 'publish' as const };
	},
	archive: async ({ locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'terms:write')) {
			return fail(403, { error: 'You do not have permission to archive terms.' });
		}
		const res = await fetch(`${apiUrl()}/terms/${params.uid}/archive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		redirect(303, '/terms');
	},
	upload: async ({ request, locals, fetch, params }) => {
		if (!can(locals.permissions, locals.role, 'terms:write')) {
			return fail(403, { error: 'You do not have permission to upload a PDF.' });
		}
		const form = await request.formData();
		const file = form.get('file');
		if (!(file instanceof File) || !file.size) return fail(400, { error: 'Choose a PDF.' });
		const body = new FormData();
		body.set('file', file);
		const res = await fetch(`${apiUrl()}/terms/${params.uid}/file`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken),
			body
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true, operation: 'upload' as const };
	}
};
