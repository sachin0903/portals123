import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { FlowImportOut } from '@graphene/api-client';
import type { FlowOut } from '@graphene/ui/book/journeys';
import { parseBundle } from '$lib/flow-bundle';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// The search lives in the URL so the API answers it — across every flow, not across the
	// ones already fetched — and so it survives a reload and can be linked.
	const query = url.searchParams.get('q')?.trim() ?? '';
	if (!can(locals.permissions, locals.role, 'flows:read')) {
		return { flows: [] as FlowOut[], canManage: false, loadError: false };
	}
	const res = await fetchJson<FlowOut[]>(
		fetch,
		`${apiUrl()}/flows?${new URLSearchParams(query ? { q: query } : {})}`,
		authHeaders(locals.accessToken)
	);
	return {
		query,
		flows: res.ok ? res.data : [],
		canManage: can(locals.permissions, locals.role, 'flows:write'),
		loadError: !res.ok
	};
};

export const actions: Actions = {
	/** Create a flow (initial empty draft). Returns the new uid so the page can open the builder. */
	create: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'flows:write')) {
			return fail(403, { error: 'You do not have permission to create flows.' });
		}
		const form = await request.formData();
		const name = String(form.get('name') ?? '').trim();
		const code = String(form.get('code') ?? '').trim();
		if (!name || !code) return fail(400, { error: 'Name and reference code are required.' });
		const description = String(form.get('description') ?? '').trim();
		const entity = String(form.get('entity') ?? '').trim();
		const res = await fetch(`${apiUrl()}/flows`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				name,
				code,
				description: description || null,
				entity: entity || null
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created: FlowOut = await res.json();
		return { ok: true, uid: created.uid };
	},

	/** Bring a flow export (`<code>.flow.json`) into this firm as a draft. The API does the work
	 *  (`POST /flows/import`: resolves forms and templates by code, creates the missing ones,
	 *  rewrites the definition, saves a draft version); this reads the file and passes it on. */
	import: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'flows:write')) {
			return fail(403, { error: 'You do not have permission to import flows.' });
		}
		const form = await request.formData();
		const file = form.get('bundle');
		if (!(file instanceof File) || file.size === 0) {
			return fail(400, { error: 'Choose a flow export to import.' });
		}
		const bundle = parseBundle(await file.text());
		if (bundle === null) return fail(400, { error: 'That file is not a flow export.' });
		const code = String(form.get('code') ?? '').trim();
		const res = await fetch(`${apiUrl()}/flows/import`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ bundle, code: code || null })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const imported: FlowImportOut = await res.json();
		return {
			ok: true,
			uid: imported.flow.uid,
			name: imported.flow.name,
			imported: {
				created: imported.created,
				forms: imported.forms,
				templates: imported.templates,
				warnings: imported.warnings
			}
		};
	},

	/** Retire a flow (or bring it back). An archived flow no longer appears in the "New journey"
	 *  picker; journeys already running keep their pinned version and finish normally. */
	setStatus: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'flows:write')) {
			return fail(403, { error: 'You do not have permission to manage flows.' });
		}
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '').trim();
		const status = String(form.get('status') ?? '').trim();
		if (!uid || !status) return fail(400, { error: 'No flow specified.' });
		const res = await fetch(`${apiUrl()}/flows/${uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ status })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
