import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { toTemplate, toVersion, type ApiTemplate, type ApiVersion } from '$lib/templates';
import { toForm, type ApiForm } from '$lib/forms';
import { toDataset, type ApiDataset } from '$lib/datasets';
import type { Template, TemplateVersion, Form, Dataset } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'templates:read')) {
		return {
			template: null as Template | null,
			versions: [] as TemplateVersion[],
			forms: [] as Form[],
			datasets: [] as Dataset[],
			canManage: false
		};
	}
	const headers = authHeaders(locals.accessToken);
	// Forms feed the input-form picker; datasets feed the "Link datasets" dialog (both real now).
	// Datasets need datasets:read — degrade to an empty list rather than failing the page if absent.
	// The preview is NOT loaded here — the page lazy-loads the true PDF render (preview.pdf) into an
	// iframe after paint, so a (multi-second) render never blocks the detail page.
	const canReadDatasets = can(locals.permissions, locals.role, 'datasets:read');
	const [tplRes, versRes, formsRes, dsRes] = await Promise.all([
		fetch(`${apiUrl()}/templates/${params.uid}`, { headers }),
		fetch(`${apiUrl()}/templates/${params.uid}/versions`, { headers }),
		fetch(`${apiUrl()}/forms`, { headers }),
		canReadDatasets ? fetch(`${apiUrl()}/datasets`, { headers }) : Promise.resolve(null)
	]);
	if (!tplRes.ok) {
		return {
			template: null,
			versions: [],
			forms: [],
			datasets: [],
			canManage: false
		};
	}
	const tpl: ApiTemplate = await tplRes.json();
	const vers: ApiVersion[] = versRes.ok ? await versRes.json() : [];
	const forms: ApiForm[] = formsRes.ok ? await formsRes.json() : [];
	const datasets: ApiDataset[] = dsRes?.ok ? await dsRes.json() : [];
	return {
		template: toTemplate(tpl),
		versions: vers.map(toVersion),
		forms: forms.map(toForm),
		datasets: datasets.map(toDataset),
		canManage: can(locals.permissions, locals.role, 'templates:write')
	};
};

async function patchTemplate(
	locals: App.Locals,
	fetch: typeof globalThis.fetch,
	uid: string,
	payload: Record<string, unknown>
) {
	if (!locals.user) return fail(401, { error: 'Not signed in.' });
	if (!can(locals.permissions, locals.role, 'templates:write')) {
		return fail(403, { error: 'You do not have permission to edit templates.' });
	}
	const res = await fetch(`${apiUrl()}/templates/${uid}`, {
		method: 'PATCH',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(payload)
	});
	if (!res.ok) return fail(res.status, { error: await detail(res) });
	return { ok: true };
}

export const actions: Actions = {
	/** Edit the template's details — name, description, document type, and renderer (the last is
	    applied to the latest version). */
	updateDetails: async ({ params, request, locals, fetch }) => {
		const form = await request.formData();
		const payload: Record<string, unknown> = {
			description: String(form.get('description') ?? '') // '' clears it
		};
		const name = String(form.get('name') ?? '').trim();
		if (name) payload.name = name;
		const dtype = String(form.get('document_type') ?? '').trim();
		if (dtype) payload.document_type = dtype;
		const renderer = String(form.get('renderer') ?? '').trim();
		if (renderer) payload.renderer = renderer;
		return patchTemplate(locals, fetch, params.uid!, payload);
	},

	/** Link/unlink datasets (dataset codes) on the template. */
	setDatasets: async ({ params, request, locals, fetch }) => {
		const codes = JSON.parse(String((await request.formData()).get('codes') ?? '[]'));
		return patchTemplate(locals, fetch, params.uid!, { linked_datasets: codes });
	},

	/** Set (or clear) the template's input form (a form code). */
	setInputForm: async ({ params, request, locals, fetch }) => {
		const code = String((await request.formData()).get('code') ?? '').trim();
		return patchTemplate(locals, fetch, params.uid!, { input_form_code: code || null });
	},

	/** Publish the template's current draft version. */
	publish: async ({ params, request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'templates:write')) {
			return fail(403, { error: 'You do not have permission to publish templates.' });
		}
		const vid = String((await request.formData()).get('vid') ?? '').trim();
		if (!vid) return fail(400, { error: 'No version specified.' });
		const res = await fetch(`${apiUrl()}/templates/${params.uid}/versions/${vid}/publish`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		// Publish succeeds even with drift; surface any unknown variables as a soft warning.
		const body = (await res.json().catch(() => ({}))) as {
			unknown_variables?: { name: string; suggestion?: string | null }[];
		};
		return { ok: true, unknownVariables: body.unknown_variables ?? [] };
	},

	/** Archive (soft-delete) the template — moves it to the Archived view; stays viewable/restorable. */
	archive: async ({ params, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'templates:write')) {
			return fail(403, { error: 'You do not have permission to archive templates.' });
		}
		const res = await fetch(`${apiUrl()}/templates/${params.uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Restore an archived template to the live library. */
	unarchive: async ({ params, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'templates:write')) {
			return fail(403, { error: 'You do not have permission to restore templates.' });
		}
		const res = await fetch(`${apiUrl()}/templates/${params.uid}/unarchive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Duplicate into a new draft. Returns the new uid so the page opens its editor. */
	duplicate: async ({ params, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'templates:write')) {
			return fail(403, { error: 'You do not have permission to duplicate templates.' });
		}
		const res = await fetch(`${apiUrl()}/templates/${params.uid}/duplicate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created: ApiTemplate = await res.json();
		return { ok: true, uid: created.uid };
	}
};
