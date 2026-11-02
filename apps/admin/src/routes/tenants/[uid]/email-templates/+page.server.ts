import { DEFAULT_CATEGORY } from '@graphene/ui/email-editor';
import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { templateScope } from '$lib/email-templates/scope';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/** The admin API's EmailTemplateOut (app/routers/email_templates.py, served cross-tenant here). */
export interface ApiEmailTemplate {
	uid: string;
	code: string;
	name: string;
	category: string;
	description: string | null;
	version_count: number;
	current_version_number: number | null;
	current_version_status: string | null;
	created_at: string;
	created_by: string | null;
}

/** A send-path code the catalogue offers as a selectable email type. */
export interface ApiEmailCode {
	code: string;
	label: string;
	category: string;
	description: string;
}

// The section is addressed by UID (the [uid] layout), but the `/admin/tenants/{tenant_id}` API keeps
function base(tenantUid: string): string {
	return `${apiUrl()}/admin/tenants/${tenantUid}/email-templates`;
}

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// No resolve: these endpoints are addressed by the uid the URL already carries.
	const scope = await templateScope(params.uid, locals.accessToken, fetch);
	if (!can(locals.permissions, locals.role, scope.read)) error(403, 'Not authorised');
	const canManage = can(locals.permissions, locals.role, scope.write);
	const headers = authHeaders(locals.accessToken);
	// One list: a template either exists or it does not, so there is no view to segment and no
	// `status` for the API to take.
	const [listRes, catalogRes] = await Promise.all([
		fetch(base(params.uid), { headers }),
		fetch(`${base(params.uid)}/catalog`, { headers })
	]);
	return {
		templates: (listRes.ok ? await listRes.json() : []) as ApiEmailTemplate[],
		catalog: (catalogRes.ok ? await catalogRes.json() : []) as ApiEmailCode[],
		canManage,
		isModel: scope.write === 'model:write',
		loadError: !listRes.ok
	};
};

/** Assert write scope on the target tenant for a form action (which has no `parent()` to read the
 *  layout's tenant from) and hand back the uid the endpoints are addressed by. Redirects back to
 *  the section on any refusal. */
async function manageContext(
	uid: string,
	locals: App.Locals,
	fetch: typeof globalThis.fetch
): Promise<string> {
	if (!locals.user) redirect(303, '/login');
	// No resolve: the endpoints are addressed by the uid this was handed, and the scope check is
	// a comparison against the model tenant's uid rather than its serial.
	const scope = await templateScope(uid, locals.accessToken, fetch);
	if (!can(locals.permissions, locals.role, scope.write))
		redirect(303, `/tenants/${uid}/email-templates`);
	return uid;
}

export const actions: Actions = {
	/** Seed this tenant with the built-in defaults for every catalogue code (idempotent) — the
	 *  one-click way to populate a preset, or to give a firm a starting point. Attributed to the
	 *  acting operator by the API. */
	loadDefaults: async ({ params, locals, fetch }) => {
		const tenantUid = await manageContext(params.uid, locals, fetch);
		const res = await fetch(`${base(tenantUid)}/load-defaults`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'loadDefaults', error: await detail(res) });
		const out = await res.json();
		return { action: 'loadDefaults', ok: true, created: out.created as number };
	},

	/** Create a template (+ empty draft). Returns the uid so the page opens the editor. */
	create: async ({ params, request, locals, fetch }) => {
		const tenantUid = await manageContext(params.uid, locals, fetch);
		const data = await request.formData();
		const code = String(data.get('code') ?? '').trim();
		const name = String(data.get('name') ?? '').trim();
		const category = String(data.get('category') ?? DEFAULT_CATEGORY);
		// WHAT THE EMAIL IS ABOUT — decides which event details the context panel lists. Posted as
		// repeated checkbox values; omitted entirely when none is ticked, so the schema gains no empty key.
		const subjects = data.getAll('subjects').map(String).filter(Boolean);
		const description = String(data.get('description') ?? '').trim() || null;
		if (!code || !name)
			return fail(400, { action: 'create', error: 'Name and code are required.' });
		const res = await fetch(base(tenantUid), {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				code,
				name,
				category,
				description,
				...(subjects.length ? { subjects } : {})
			})
		});
		if (!res.ok) return fail(res.status, { action: 'create', error: await detail(res) });
		const created: ApiEmailTemplate = await res.json();
		return { action: 'create', ok: true, uid: created.uid };
	},

	/** Edit a template's metadata (name / code / description) — so a mistyped code on the preset, or
	 *  on a firm's copy, is a correction rather than a delete-and-recreate that loses every version
	 *  authored against the wrong code. PATCH is a partial
	 *  update, and a duplicate code comes back 409 for the settings dialog to surface inline.
	 *
	 *  `category` is deliberately NOT sent, exactly as on the firm's own surface: nothing reads it at
	 *  send time, so neither form asks — and since the API coalesces, posting a defaulted
	 *  `notification` would silently reset a transactional template's category on every rename.
	 *
	 *  `description` posts the trimmed string, blank included, for the mirror-image reason: it is the
	 *  one field a user can legitimately EMPTY, and `trim() || null` into a COALESCE means clearing a
	 *  description restores the old one and reports success. Only an explicit `''` clears it. */
	update: async ({ params, request, locals, fetch }) => {
		const tenantUid = await manageContext(params.uid, locals, fetch);
		const data = await request.formData();
		const uid = String(data.get('uid') ?? '');
		const code = String(data.get('code') ?? '').trim();
		const name = String(data.get('name') ?? '').trim();
		const description = String(data.get('description') ?? '').trim();
		if (!uid) return fail(400, { action: 'update', error: 'No template specified.' });
		if (!code || !name)
			return fail(400, { action: 'update', error: 'Name and code are required.' });
		const res = await fetch(`${base(tenantUid)}/${uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ code, name, description })
		});
		if (!res.ok) return fail(res.status, { action: 'update', error: await detail(res) });
		return { action: 'update', ok: true };
	},

	/** Destroy a template and its version history on the firm's behalf, freeing its `code`.
	 *
	 *  The API's refusals are the copy: 409 NAMES the published journey or automation that still sends
	 *  the code, or says the code has no shipped default to fall back on. `detail(res)` forwards that
	 *  sentence to the listing's toast unchanged — a generic "could not delete" would hide the one
	 *  thing the operator has to go and fix. */
	delete: async ({ params, request, locals, fetch }) => {
		const tenantUid = await manageContext(params.uid, locals, fetch);
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { action: 'delete', error: 'No template specified.' });
		const res = await fetch(`${base(tenantUid)}/${uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'delete', error: await detail(res) });
		return { action: 'delete', ok: true };
	}
};
