import { error, fail, redirect } from '@sveltejs/kit';
import type { LookthroughAdminData, LookthroughImportResult } from '@graphene/ui/book/lookthrough';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'lookthrough:read')) error(403, 'Not authorised');
	const res = await fetch(`${apiUrl()}/admin/look-through`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, 'Could not load look-through imports');
	return {
		lookthrough: (await res.json()) as LookthroughAdminData,
		canImport: can(locals.permissions, locals.role, 'lookthrough:write')
	};
};

export const actions: Actions = {
	default: async ({ locals, request, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Sign in required.' });
		if (!can(locals.permissions, locals.role, 'lookthrough:write'))
			return fail(403, { error: 'You do not have permission to import look-through files.' });
		const incoming = await request.formData();
		const files = incoming.getAll('files').filter((value): value is File => value instanceof File);
		if (!files.length) return fail(400, { error: 'Choose at least one XLSX file.' });
		const body = new FormData();
		for (const file of files) body.append('files', file, file.name);
		const res = await fetch(`${apiUrl()}/admin/look-through/import`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken),
			body
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const payload = (await res.json()) as { results: LookthroughImportResult[] };
		return { success: true, results: payload.results };
	}
};
