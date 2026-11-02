import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { Actions, PageServerLoad } from './$types';

interface ApiTemplate {
	uid: string;
	code: string;
	name: string;
	category: string;
	description: string | null;
}
interface ApiVersion {
	uid: string;
	version_number: number;
	status: string;
	subject: string;
	preheader: string;
}
/** A publish answers the version PLUS a soft warning: merge tokens nothing supplies, which render
 *  blank on every send. Publish still succeeded. */
interface ApiPublished extends ApiVersion {
	unsourced?: string[];
}
interface ApiVersionDetail extends ApiVersion {
	// The editor's own jsonb. `source` is the editor's copy of the body and `subjects` what the email
	// is about; both are opaque to the API and must be DECLARED here or the editor never sees them
	// (the shape is what the page mounts, not what the response happens to carry).
	schema: { html?: string; css?: string; source?: string; subjects?: unknown };
}
/** The built-in default for this template's code, tokenised — for seeding a blank canvas. Mirrors the
 *  API's `EmailDefaultOut`: every field it returns must be declared here, or the editor never sees
 *  it. `preheader` is the case in point — it is what "Load default" fills the inbox preview line
 *  from, and a local shape that omits it drops the value before the page mounts. */
interface ApiEmailDefault {
	subject: string;
	preheader: string;
	html: string;
	css: string;
}

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'email_templates:read'))
		redirect(303, '/templates/emails');
	const headers = authHeaders(locals.accessToken);

	const tplRes = await fetchJson<ApiTemplate>(
		fetch,
		`${apiUrl()}/email-templates/${params.uid}`,
		headers
	);
	if (!tplRes.ok) throw error(404, 'Email template not found');

	// Versions + the built-in default load together; the default seeds a blank canvas ("Load default")
	// and 404s (→ null) for a custom code with no shipped default.
	const [versionsRes, defaultRes] = await Promise.all([
		fetchJson<ApiVersion[]>(fetch, `${apiUrl()}/email-templates/${params.uid}/versions`, headers),
		fetchJson<ApiEmailDefault>(fetch, `${apiUrl()}/email-templates/${params.uid}/default`, headers)
	]);
	const head = versionsRes.ok && versionsRes.data.length ? versionsRes.data[0] : null;

	let version: ApiVersionDetail | null = null;
	if (head) {
		const dRes = await fetchJson<ApiVersionDetail>(
			fetch,
			`${apiUrl()}/email-templates/${params.uid}/versions/${head.uid}`,
			headers
		);
		version = dRes.ok ? dRes.data : null;
	}

	return {
		template: tplRes.data,
		version,
		defaultContent: defaultRes.ok ? defaultRes.data : null,
		canManage: can(locals.permissions, locals.role, 'email_templates:write')
	};
};

function requireManage(locals: App.Locals): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'email_templates:write'))
		redirect(303, '/templates/emails');
}

export const actions: Actions = {
	/** Save the editor content (autosave). Updates the head draft in place, or rolls a new draft. */
	save: async ({ params, request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		// WHAT THE EMAIL IS ABOUT rides inside the same `schema` jsonb, posted as JSON. A BLANK field
		// means "claims none", and the key is then left off the payload entirely so an untouched
		// template's schema never acquires one. Unparseable JSON is refused rather than dropped: the
		// editor writes this field, so a failure here is a bug, and silently saving without an author's
		// claim is the worst way to report one.
		const claimed = String(data.get('subjects') ?? '').trim();
		let subjects: unknown;
		try {
			subjects = claimed ? JSON.parse(claimed) : undefined;
		} catch {
			return fail(400, { action: 'save', error: 'The email’s subjects could not be read.' });
		}
		const res = await fetch(`${apiUrl()}/email-templates/${params.uid}/versions`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				subject: String(data.get('subject') ?? ''),
				preheader: String(data.get('preheader') ?? ''),
				// `source` is the EDITOR's own copy and `html` is the render-ready one. They differ only
				// where an author has used a condition: `source` keeps the `data-if` attributes the panel
				// edits, and `html` has them compiled into Jinja and stripped.
				//
				// Both are stored because neither can be derived from the other at the point it is needed:
				// the send path reads `html` and must never see editor bookkeeping, and re-opening the
				// template needs the structured condition back, which parsing Jinja could not give us.
				// `schema` is opaque jsonb on the API, so this is additive — an older row with no `source`
				// still loads, from `html`.
				schema: {
					html: String(data.get('html') ?? ''),
					css: String(data.get('css') ?? ''),
					source: String(data.get('source') ?? ''),
					...(subjects === undefined ? {} : { subjects })
				}
			})
		});
		if (!res.ok) return fail(res.status, { action: 'save', error: await detail(res) });
		const v: ApiVersion = await res.json();
		return {
			action: 'save',
			ok: true,
			vid: v.uid,
			versionNumber: v.version_number,
			status: v.status
		};
	},

	/** Publish a draft version. Requires a subject + content (enforced by the API). */
	publish: async ({ params, request, locals, fetch }) => {
		requireManage(locals);
		const vid = String((await request.formData()).get('vid') ?? '');
		if (!vid) return fail(400, { action: 'publish', error: 'Save the draft first.' });
		const res = await fetch(`${apiUrl()}/email-templates/${params.uid}/versions/${vid}/publish`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'publish', error: await detail(res) });
		const v: ApiPublished = await res.json();
		// `unsourced` forwarded, not dropped: the API answers it on the publish and this is the only
		// place it can reach the author. Declared on the type or the response's own field is invisible
		// here — the shape is what the page reads, not what the JSON happens to carry.
		return { action: 'publish', ok: true, status: v.status, unsourced: v.unsourced ?? [] };
	}
};
