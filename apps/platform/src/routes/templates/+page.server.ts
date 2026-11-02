import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { DEFAULT_CATEGORY } from '@graphene/ui/email-editor';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import { toTemplate, type ApiTemplate } from '$lib/templates';
import { toForm, type ApiForm } from '$lib/forms';
import { toDataset, type ApiDataset } from '$lib/datasets';
import type { Template, Form, Dataset } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

/**
 * ONE KIND PER LOAD. Documents and Emails used to ride together so the tab switch was a view over
 * data already on screen. That made the Documents tab wait on the email catalogue (and on the
 * notification-rules map that only the delete confirm needs). The kind is in the query
 * (`?kind=emails`) so the view is linkable; switching it re-runs this loader, which is the trade
 * for a first paint that is only the tab you asked for. Search (`?q=`) is the API's to answer, on
 * the documents slice, for the same reason it always was: a client-side filter over one page would
 * say "no template called Statement" when it means "not on this page".
 */

/** The API's EmailTemplateOut (app/routers/email_templates.py). */
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

/** The API's EmailCodeOut (app/routers/email_templates.py) — a code the send path actually resolves,
 *  drawn from the catalogue in app/email/catalog.py. */
export interface ApiEmailCode {
	code: string;
	label: string;
	category: string;
	description: string;
}

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// The search lives in the URL so the API answers it — across every row, not across the ones
	// already fetched — and so it survives a reload and can be linked.
	const query = url.searchParams.get('q')?.trim() ?? '';
	// ALL THREE grants up front, because each is a VIEW of this page and each is separate: a caller
	// may hold any one of them and none of the others. An absent grant empties that view's slice and
	// nothing else — and the refusal below has to weigh all three, or holding only one of them lands
	// on a page that returns nothing while its own tab says it should be readable.
	const canDocuments = can(locals.permissions, locals.role, 'templates:read');
	const canEmails = can(locals.permissions, locals.role, 'email_templates:read');
	const canReadDatasets = can(locals.permissions, locals.role, 'datasets:read');
	if (!canDocuments && !canEmails && !canReadDatasets) {
		return {
			templates: [] as Template[],
			archivedTemplates: [] as Template[],
			forms: [] as Form[],
			datasets: [] as Dataset[],
			canManage: false,
			// All false by construction — this branch is only reached when no grant is held, and the
			// switch draws a tab only for a view that can actually be opened.
			canDocuments: false,
			canEmails: false,
			canDatasets: false,
			canManageDatasets: false,
			emailTemplates: [] as ApiEmailTemplate[],
			emailCatalog: [] as ApiEmailCode[],
			canManageEmails: false,
			loadError: false
		};
	}
	const askedEmails = url.searchParams.get('kind') === 'emails';
	// Asked-for emails with no email grant falls back to documents, matching the page's tab logic.
	const loadEmails = canEmails && (askedEmails || !canDocuments);
	const loadDocuments = canDocuments && (!askedEmails || !canEmails);
	const headers = authHeaders(locals.accessToken);
	// Forms feed the create wizard's input-form step; datasets feed its dataset step. Both belong
	// to Documents — an email template draws on neither. Datasets also stay reachable from this
	// page's button when the caller can read datasets but not documents.
	const loadDatasets = canReadDatasets && (loadDocuments || !loadEmails);
	const [tplRes, archivedRes, formsRes, dsRes, emailRes, catalogRes] = await Promise.all([
		loadDocuments
			? fetchJson<ApiTemplate[]>(
					fetch,
					`${apiUrl()}/templates?${new URLSearchParams(query ? { q: query } : {})}`,
					headers
				)
			: null,
		loadDocuments
			? fetchJson<ApiTemplate[]>(
					fetch,
					`${apiUrl()}/templates?status=archived&${new URLSearchParams(query ? { q: query } : {})}`,
					headers
				)
			: null,
		loadDocuments ? fetchJson<ApiForm[]>(fetch, `${apiUrl()}/forms`, headers) : null,
		loadDatasets ? fetchJson<ApiDataset[]>(fetch, `${apiUrl()}/datasets`, headers) : null,
		loadEmails
			? fetchJson<ApiEmailTemplate[]>(fetch, `${apiUrl()}/email-templates`, headers)
			: null,
		loadEmails
			? fetchJson<ApiEmailCode[]>(fetch, `${apiUrl()}/email-templates/catalog`, headers)
			: null
	]);
	// A failed fetch surfaces as a retry banner rather than an empty page (`null` = permission skip
	// or the other kind, not asked for this load).
	const loadError = [tplRes, archivedRes, formsRes, dsRes, emailRes, catalogRes].some(
		(r) => r !== null && !r.ok
	);
	return {
		query,
		templates: tplRes?.ok ? tplRes.data.map(toTemplate) : [],
		archivedTemplates: archivedRes?.ok ? archivedRes.data.map(toTemplate) : [],
		forms: formsRes?.ok ? formsRes.data.map(toForm) : [],
		datasets: dsRes?.ok ? dsRes.data.map(toDataset) : [],
		canManage: can(locals.permissions, locals.role, 'templates:write'),
		canDocuments,
		canEmails,
		canDatasets: canReadDatasets,
		canManageDatasets: can(locals.permissions, locals.role, 'datasets:write'),
		emailTemplates: emailRes?.ok ? emailRes.data : [],
		emailCatalog: catalogRes?.ok ? catalogRes.data : [],
		canManageEmails: can(locals.permissions, locals.role, 'email_templates:write'),
		loadError
	};
};

/** The email actions' own write gate. Separate from the documents ones because it is a separate
 *  grant: a firm may let someone author its emails and not its documents. */
function requireEmailManage(locals: App.Locals): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'email_templates:write'))
		redirect(303, '/templates?kind=emails');
}

export const actions: Actions = {
	/** Create a template (+ its initial draft version). Returns the uid so the page opens the editor. */
	create: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'templates:write')) {
			return fail(403, { error: 'You do not have permission to create templates.' });
		}
		let input: Record<string, unknown>;
		try {
			input = JSON.parse(String((await request.formData()).get('input') ?? ''));
		} catch {
			return fail(400, { error: 'Malformed template.' });
		}
		const res = await fetch(`${apiUrl()}/templates`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				code: input.code,
				name: input.name,
				document_type: input.documentType,
				renderer: input.renderer ?? 'weasy',
				description: input.description ?? null,
				linked_datasets: input.linkedDatasets ?? [],
				input_form_code: input.inputFormCode ?? null
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created: ApiTemplate = await res.json();
		return { ok: true, uid: created.uid };
	},

	/** Archive (soft-delete) a template. It moves to the Archived view, still viewable, restorable. */
	archive: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'templates:write')) {
			return fail(403, { error: 'You do not have permission to archive templates.' });
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No template specified.' });
		const res = await fetch(`${apiUrl()}/templates/${uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Duplicate a template into a new draft. Returns the new uid so the page opens its editor. */
	duplicate: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'templates:write')) {
			return fail(403, { error: 'You do not have permission to duplicate templates.' });
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No template specified.' });
		const res = await fetch(`${apiUrl()}/templates/${uid}/duplicate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created: ApiTemplate = await res.json();
		return { ok: true, uid: created.uid };
	},

	/** Restore an archived template back to the live library. */
	unarchive: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'templates:write')) {
			return fail(403, { error: 'You do not have permission to restore templates.' });
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No template specified.' });
		const res = await fetch(`${apiUrl()}/templates/${uid}/unarchive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Create an email template (+ its initial draft). Returns the uid so the page opens the editor. */
	emailCreate: async ({ request, locals, fetch }) => {
		requireEmailManage(locals);
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
		const res = await fetch(`${apiUrl()}/email-templates`, {
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

	/** Edit a template's metadata (name / code / description). PATCH is a partial update; a duplicate
	 *  code comes back 409 for the dialog to surface.
	 *
	 *  The API's update is a COALESCE, so an ABSENT key preserves the stored value and a present one
	 *  overwrites it. Both fields below turn on that:
	 *
	 *  `category` is deliberately NOT sent. Neither the settings dialog nor the create form asks for it
	 *  (nothing reads it at send time — see `categoryFor`), so posting a defaulted `notification` would
	 *  reset a transactional template's category every time someone corrected its name.
	 *
	 *  `description` is the opposite case and posts the trimmed string, blank included: it is the one
	 *  field a user can legitimately EMPTY, and `trim() || null` into a COALESCE means clearing a
	 *  description restores the old one and reports success. Only an explicit `''` clears it. */
	emailUpdate: async ({ request, locals, fetch }) => {
		requireEmailManage(locals);
		const data = await request.formData();
		const uid = String(data.get('uid') ?? '');
		const code = String(data.get('code') ?? '').trim();
		const name = String(data.get('name') ?? '').trim();
		const description = String(data.get('description') ?? '').trim();
		if (!uid) return fail(400, { action: 'update', error: 'No template specified.' });
		if (!code || !name)
			return fail(400, { action: 'update', error: 'Name and code are required.' });
		const res = await fetch(`${apiUrl()}/email-templates/${uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ code, name, description })
		});
		if (!res.ok) return fail(res.status, { action: 'update', error: await detail(res) });
		return { action: 'update', ok: true };
	},

	/** Delete a template and its version history, for good — the code is then free to use again.
	 *
	 *  The API's refusals are the point of surfacing `detail` rather than a message of our own: a 409
	 *  NAMES the published journey or automation that still sends this template, or says the code has
	 *  no built-in default to fall back on. Nothing on this page links a template to a journey, so a
	 *  generic "could not delete" would leave the author hunting for a reference they cannot see.
	 *
	 *  The answer carries the destroyed row, so the surface can name what went. */
	emailDelete: async ({ request, locals, fetch }) => {
		requireEmailManage(locals);
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { action: 'delete', error: 'No template specified.' });
		const res = await fetch(`${apiUrl()}/email-templates/${uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'delete', error: await detail(res) });
		const removed = (await res.json().catch(() => null)) as ApiEmailTemplate | null;
		return { action: 'delete', ok: true, name: removed?.name ?? null };
	}
};
