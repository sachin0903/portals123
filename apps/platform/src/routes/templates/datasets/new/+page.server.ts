import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import { parseInlineTable } from '$lib/csv';
import type { ApiCatalogueEntry } from '$lib/datasets';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'datasets:write')) error(403, 'Not permitted');
	// The provided entries and what each lets a firm set. Loaded here rather than hard-coded so a
	// new entry — or a new setting on an existing one — appears in this form the moment it ships.
	const res = await fetchJson<ApiCatalogueEntry[]>(
		fetch,
		`${apiUrl()}/datasets/catalogue`,
		authHeaders(locals.accessToken)
	);
	return {
		catalogue: res.ok ? res.data : [],
		catalogueError: !res.ok,
		// A firm-wide dataset is run across the WHOLE firm when a document is generated, so the API
		// requires 'contacts:read_all' to set one up (403 otherwise). Without it the form offers the
		// per-client entries only, with the reason — rather than a control that earns a 403.
		canFirmWide: can(locals.permissions, locals.role, 'contacts:read_all')
	};
};

export const actions: Actions = {
	default: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'datasets:write')) error(403, 'Not permitted');
		const form = await request.formData();
		const format = String(form.get('format') ?? 'inline');
		// Echo the submitted values back so the form survives a validation error.
		const values = {
			code: String(form.get('code') ?? '').trim(),
			name: String(form.get('name') ?? '').trim(),
			description: String(form.get('description') ?? '').trim(),
			format,
			scope: String(form.get('scope') ?? 'global'),
			catalogue_key: String(form.get('catalogue_key') ?? ''),
			columns: String(form.get('columns') ?? ''),
			rows: String(form.get('rows') ?? '')
		};

		const body: Record<string, unknown> = {
			code: values.code,
			name: values.name,
			description: values.description || null,
			format,
			scope: values.scope
		};
		if (format === 'catalogue') {
			// Every `param_*` field, posted VERBATIM. A relative range (`-12m`) has to survive to the
			// database unchanged — resolving it here would freeze the dataset on today's date.
			const params: Record<string, string> = {};
			for (const [key, raw] of form.entries()) {
				if (!key.startsWith('param_')) continue;
				const value = String(raw);
				// An empty setting means "no bound", which the API stores as such. Sending the key with
				// an empty value says that explicitly rather than falling back to the shipped default.
				params[key.slice('param_'.length)] = value;
			}
			body.catalogue = { key: values.catalogue_key, params };
		} else if (format === 'inline') {
			// Quote-aware parse + ragged-row check: a comma inside an unquoted value (e.g. £325,000)
			// would otherwise split into extra cells and silently corrupt the dataset.
			const { columns, rows, raggedRows } = parseInlineTable(values.columns, values.rows);
			if (raggedRows.length) {
				const where = raggedRows.map((r) => `line ${r.line} (${r.got} values)`).join(', ');
				return fail(400, {
					error:
						`These rows don't match the ${columns.length} column${columns.length === 1 ? '' : 's'}: ` +
						`${where}. Wrap any value containing a comma in double quotes, for example "£325,000".`,
					values
				});
			}
			body.inline = { columns, rows };
		}

		const res = await fetch(`${apiUrl()}/datasets`, {
			method: 'POST',
			headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
			body: JSON.stringify(body)
		});
		if (!res.ok) {
			const detail = await res.json().catch(() => ({}));
			return fail(res.status, {
				error:
					res.status === 403
						? 'You cannot create a firm-wide dataset. Ask an administrator for access to all clients.'
						: ((detail as { detail?: string }).detail ?? 'Could not create the dataset.'),
				values
			});
		}
		const created = (await res.json()) as { uid: string };
		redirect(303, `/templates/datasets/${created.uid}`);
	}
};
