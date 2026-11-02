import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { ContentItem, ContentKind } from '$lib/content';
import type { Actions, PageServerLoad } from './$types';

/** The library's rows — or that the read failed. A failed read is never an empty library. */
export type ContentFeed = { items: ContentItem[]; loadError: boolean };

const denied: ContentFeed = { items: [], loadError: false };

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	const q = url.searchParams.get('q')?.trim() ?? '';
	// MULTI-VALUED: `getAll`, not `get` — "Article or Guide" is one question, not two.
	const kind = url.searchParams
		.getAll('kind')
		.map((k) => k.trim())
		.filter(Boolean);
	const status = url.searchParams
		.getAll('status')
		.map((s) => s.trim())
		.filter(Boolean);
	const view = url.searchParams.get('view') === 'archived' ? 'archived' : 'current';
	// Fetch every status for the selected search/kind. The page separates archived items into its
	// own tab; sending `status=archived` here would leave the Current tab empty after switching.
	const echoedStatus = view === 'current' ? status : [];

	// The read gate is SYNCHRONOUS and returns a resolved feed, so the permission alert paints
	// without a round trip and without a skeleton. The query echo is resolved here either way:
	// the toolbar paints from it the moment the page is navigated to, and the rows stream behind.
	if (!can(locals.permissions, locals.role, 'content:read')) {
		return {
			q,
			kind,
			status: echoedStatus,
			view,
			canManage: false,
			feed: denied
		};
	}

	return {
		q,
		kind,
		status: echoedStatus,
		view,
		canManage: can(locals.permissions, locals.role, 'content:write'),
		feed: loadFeed(locals.accessToken, fetch, q, kind, echoedStatus)
	};
};

async function loadFeed(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	q: string,
	kind: string[],
	status: string[]
): Promise<ContentFeed> {
	const params = new URLSearchParams();
	if (q) params.set('q', q);
	for (const k of kind) params.append('kind', k);
	for (const s of status) params.append('status', s);
	const qs = params.toString();
	const res = await fetchJson<ContentItem[]>(
		fetch,
		`${apiUrl()}/content/items${qs ? `?${qs}` : ''}`,
		authHeaders(accessToken)
	);
	// A failed fetch surfaces as a retry banner rather than an empty library (a transient failure
	// must not read as "no content").
	return { items: res.ok ? res.data : [], loadError: !res.ok };
}

export const actions: Actions = {
	create: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'content:write')) {
			return fail(403, { error: 'You do not have permission to create content.' });
		}
		const form = await request.formData();
		const title = String(form.get('title') ?? '').trim();
		const kind = String(form.get('kind') ?? 'article').trim() as ContentKind;
		if (!title) return fail(400, { error: 'Title is required.' });
		const res = await fetch(`${apiUrl()}/content/items`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ title, kind })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created = (await res.json()) as ContentItem;
		redirect(303, `/content/${created.uid}/edit`);
	}
};
