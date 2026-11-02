import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { templateScope } from '$lib/email-templates/scope';
import { can } from '$lib/permissions';
import type { Branding } from '@graphene/auth';
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
/** Mirrors the API's `EmailDefaultOut`. The response is `as`-cast onto this shape, so every field the
 *  editor needs must be declared — `preheader` above all: it exists so "Load default" fills the inbox
 *  preview line, and omitting it here drops the value before the page mounts. */
interface ApiEmailDefault {
	subject: string;
	preheader: string;
	html: string;
	css: string;
}

function base(tenantUid: string): string {
	return `${apiUrl()}/admin/tenants/${tenantUid}/email-templates`;
}

/**
 * The TARGET tenant's palette, for the editor's brand-colour swatches.
 *
 * It has to be fetched: the admin portal wears the Graphene PRODUCT look and is deliberately not
 * tenant-themed (CLAUDE.md), so `page.data.branding` here is Graphene's own and would offer an
 * operator the wrong firm's colours to write into a firm's email. Sourced exactly as the sibling
 * branding surface does (`/tenants/[uid]/branding` → `loadPortal`), and `portal=platform` matches
 * what the firm's own editor reads off its host-resolved branding, so both surfaces offer the same
 * swatches. (A firm's palette is written to both portals together by the studio.)
 *
 * Only `colors` is carried through — `pages`/`layout` can be large and the editor wants a palette.
 * Any failure degrades to no swatches: an unreachable branding record must not 500 the editor.
 */
async function brandingPalette(
	fetch: typeof globalThis.fetch,
	tenantUid: string,
	token: string | null
): Promise<Branding | null> {
	try {
		const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/branding?portal=platform`, {
			headers: authHeaders(token)
		});
		if (!res.ok) return null;
		const body: { branding?: Branding | null } = await res.json();
		// Two independent fields, and the guard has to allow either alone: `colors` feeds the editor's
		// colour swatches and `logo_url` its masthead preview — the canvas shows the arm THIS firm will
		// actually get rather than both plus a broken image. Keyed on `colors` alone, a firm with a logo
		// and no palette returned null and its operator previewed the wrong masthead.
		// FOUR FIELDS, because the masthead is a CHOICE between two images and the firm makes it.
		// `emailMastheadPreview` mirrors the send's own rule — `(icon or wordmark)` when the firm's
		// `logo_variant` is `icon`, `(wordmark or icon)` otherwise — and this projection carried
		// `logo_url` alone, so a firm that uploaded a mark and asked for the mark treatment had an
		// operator previewing its lockup, or nothing at all where it holds only a mark. Keeping the
		// projection narrow is deliberate (the canvas needs no palette, no copy, no pages); it just
		// has to carry every field the masthead decision reads.
		const colors = body.branding?.colors;
		const logo = body.branding?.logo_url;
		const icon = body.branding?.icon_url;
		const variant = body.branding?.logo_variant;
		return colors || logo || icon
			? {
					...(colors ? { colors } : {}),
					...(logo ? { logo_url: logo } : {}),
					...(icon ? { icon_url: icon } : {}),
					...(variant ? { logo_variant: variant } : {})
				}
			: null;
	} catch {
		return null;
	}
}

export const load: PageServerLoad = async ({ params, locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const scope = await templateScope(params.uid, locals.accessToken, fetch);
	if (!can(locals.permissions, locals.role, scope.read)) error(403, 'Not authorised');
	const headers = authHeaders(locals.accessToken);
	const uid = tenant.uid;

	const tplRes = await fetch(`${base(uid)}/${params.templateUid}`, { headers });
	if (!tplRes.ok) throw error(404, 'Email template not found');
	const template: ApiTemplate = await tplRes.json();

	const isModel = scope.write === 'model:write';

	// Versions + the built-in default load together; the default seeds a blank canvas ("Load default")
	// and 404s (→ null) for a custom code with no shipped default. The tenant's palette rides along.
	//
	// The MODEL PRESET gets no swatches, deliberately — not "platform defaults". Every firm clones this
	// preset, and a swatch writes a LITERAL: seeding it from any one palette would bake that palette
	// into the email each firm inherits, which is the white-label failure the branding system exists to
	// prevent. The model tenant also has no branding of its own to offer (no logins, no clients, and no
	// firm sends under its name — the same reason `senderName` stays "Your firm" here).
	const [versionsRes, defaultRes, branding] = await Promise.all([
		fetch(`${base(uid)}/${params.templateUid}/versions`, { headers }),
		fetch(`${base(uid)}/${params.templateUid}/default`, { headers }),
		isModel ? Promise.resolve(null) : brandingPalette(fetch, uid, locals.accessToken)
	]);
	const versions: ApiVersion[] = versionsRes.ok ? await versionsRes.json() : [];
	const head = versions.length ? versions[0] : null;

	let version: ApiVersionDetail | null = null;
	if (head) {
		const dRes = await fetch(`${base(uid)}/${params.templateUid}/versions/${head.uid}`, {
			headers
		});
		version = dRes.ok ? await dRes.json() : null;
	}

	return {
		template,
		version,
		defaultContent: defaultRes.ok ? ((await defaultRes.json()) as ApiEmailDefault) : null,
		canManage: can(locals.permissions, locals.role, scope.write),
		// The target tenant's palette (null for the model preset, or when branding is unreadable) — the
		// page derives the editor's swatches from it.
		branding,
		// So the chrome can name what it is editing. This surface serves ANY tenant; saying "Model
		// email templates" while an operator edits a firm's copy is actively misleading about whose
		// clients are about to receive it.
		isModel
	};
};

/** Resolve the target tenant's numeric id + assert write scope for an action (no `parent()`). */
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
	/** Save the editor content (autosave). Updates the head draft in place, or rolls a new draft. */
	save: async ({ params, request, locals, fetch }) => {
		const uid = await manageContext(params.uid, locals, fetch);
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
		const res = await fetch(`${base(uid)}/${params.templateUid}/versions`, {
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
		return { action: 'save', ok: true, vid: v.uid, status: v.status };
	},

	/** Publish a draft version. Requires a subject + content (enforced by the API). */
	publish: async ({ params, request, locals, fetch }) => {
		const uid = await manageContext(params.uid, locals, fetch);
		const vid = String((await request.formData()).get('vid') ?? '');
		if (!vid) return fail(400, { action: 'publish', error: 'Save the draft first.' });
		const res = await fetch(`${base(uid)}/${params.templateUid}/versions/${vid}/publish`, {
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
