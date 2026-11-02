import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { isOpen, type TaskRow, type TaskScope } from '@graphene/ui/book/tasks';
import type { Actions, PageServerLoad } from './$types';

/** The inbox's page size — the product-wide listing default (`@graphene/ui/book/listing`), so the
 *  inbox pages like every other listing. It used to ask for 200, which the page then said it was
 *  doing and could page past; the window was so wide that "next" was unreachable in practice. */
const PAGE = PAGE_SIZE;

/** Everything the inbox has to fetch, in one streamed object. */
type Inbox = {
	tasks: TaskRow[];
	mineUids: Set<string>;
	/** Open tasks in the scope the reader is NOT on, for that tab's count and for the empty
	 *  state's hint. */
	otherOpenCount: number;
	nextBefore: number | null;
};

export const load: PageServerLoad = async ({ url, locals, fetch, depends }) => {
	if (!locals.user) redirect(303, '/login');
	depends('app:tasks');
	// The permission and the filters are resolved from the session and the URL, so the inbox's shape
	// is on screen on the click; the five reads behind it are STREAMED. Both refusals stay out here —
	// a 403 and a 303 have to be thrown before the response starts, not from inside a promise.
	if (!can(locals.permissions, locals.role, 'tasks:read')) {
		error(403, 'You do not have permission to view tasks.');
	}
	// Server-side filters, driven by the URL so they survive a reload and can be shared. The API has
	// always supported these; the inbox exposed none of them.
	// Each of these may be given SEVERAL times (`?category=a&category=b`) — the filter bar offers
	// several and the question is an OR.
	const category = url.searchParams.getAll('category').filter(Boolean);
	// WHEN it is due, as windows rather than a boolean: overdue / today / week. `overdue=1` is
	// still read so a bookmark or a link from before this change still means what it said.
	const due = url.searchParams.getAll('due').filter(Boolean);
	if (!due.length && url.searchParams.get('overdue') === '1') due.push('overdue');
	// The search and the priority, applied by the API for the same reason the other two are: the
	// inbox holds one page and keysets backwards, so narrowing the rows in hand would answer for
	// the window rather than for the book.
	const q = url.searchParams.get('q')?.trim() ?? '';
	const priority = url.searchParams.getAll('priority').filter(Boolean);
	const beforeId = url.searchParams.get('before') ?? '';
	// WHOSE tasks, defaulting to the reader's own. An inbox that opens on the whole firm's work is
	// one nobody can act from — and the API applies this (`assignee=me`, which covers a role the
	// reader holds), so it narrows the BOOK. Slicing the window instead answered "you have nothing"
	// whenever none of the newest 50 happened to be yours.
	const assignee: TaskScope = url.searchParams.get('assignee') === 'all' ? 'all' : 'me';
	return {
		canManage: can(locals.permissions, locals.role, 'tasks:write'),
		filters: { category, due, q, priority, assignee },
		inbox: loadInbox(locals.accessToken, fetch, {
			category,
			due,
			q,
			priority,
			beforeId,
			assignee
		})
	};
};

async function loadInbox(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	{
		category,
		due,
		q,
		priority,
		beforeId,
		assignee
	}: {
		category: string[];
		due: string[];
		q: string;
		priority: string[];
		beforeId: string;
		assignee: TaskScope;
	}
): Promise<Inbox> {
	const h = authHeaders(accessToken);
	const query = new URLSearchParams({ limit: String(PAGE) });
	// `append`, not `set`: repeated params are how a multi-valued filter reaches the API.
	for (const value of category) query.append('category', value);
	for (const value of due) query.append('due', value);
	if (q) query.set('q', q);
	for (const value of priority) query.append('priority', value);
	if (beforeId) query.set('before_id', beforeId);
	// The rows, in the reader's own scope. `assignee=me` is the API's: assigned to the caller OR to
	// a role they hold.
	if (assignee === 'me') query.set('assignee', 'me');
	// The OTHER scope, read alongside it — for the count on the tab you are not on, for the empty
	// state's hint, and (while reading everyone's) for badging the rows that are yours. One extra
	// read, in parallel, and the inbox needed it anyway.
	const otherQuery = new URLSearchParams(query);
	if (assignee === 'me') otherQuery.delete('assignee');
	else otherQuery.set('assignee', 'me');

	// The pickers the create dialog needs load when it opens (`/tasks/pickers`), not on first
	// paint of the inbox.
	const [all, other] = await Promise.all([
		fetchJson<TaskRow[]>(fetch, `${apiUrl()}/tasks?${query}`, h),
		fetchJson<TaskRow[]>(fetch, `${apiUrl()}/tasks?${otherQuery}`, h)
	]);
	// A failed read is thrown from inside the stream, so it surfaces as the page's own retry banner
	// rather than SvelteKit's error page: the toolbar and the filters the user just set stay on
	// screen, and an empty inbox never means "the read failed".
	if (!all.ok) error(all.status, 'Could not load tasks.');
	// A full page means there is very likely more behind it; keyset-paginate on the lowest id.
	const full = all.data.length === PAGE;
	const otherRows = other.ok ? other.data : [];
	// Whichever scope is NOT on screen: reading your own, that set is everyone else's; reading
	// everyone's, it is yours — which is also what the "Yours" badge reads.
	const mineRows = assignee === 'me' ? all.data : otherRows;
	return {
		tasks: all.data,
		mineUids: new Set(mineRows.map((t) => t.uid)),
		otherOpenCount: otherRows.filter(isOpen).length,
		nextBefore: full ? Math.min(...all.data.map((t) => t.id)) : null
	};
}

export const actions: Actions = {
	create: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'tasks:write')) {
			return fail(403, { error: 'You do not have permission to create tasks.' });
		}
		const form = await request.formData();
		const title = String(form.get('title') ?? '').trim();
		const assignee = String(form.get('assignee') ?? ''); // 'user:{uuid}' | 'role:{slug}' | 'contact:{id}'
		const [kind, ...rest] = assignee.split(':');
		const ident = rest.join(':');
		if (!title || !kind || !ident) return fail(400, { error: 'A title and assignee are needed.' });
		const body: Record<string, unknown> = {
			title,
			assignee_kind: kind,
			category: String(form.get('category') ?? 'generic'),
			priority: String(form.get('priority') ?? 'normal'),
			description: String(form.get('description') ?? '').trim() || null,
			due_date: String(form.get('due_date') ?? '') || null
		};
		if (kind === 'user') body.assignee_user_id = ident;
		else if (kind === 'role') body.assignee_role_slug = ident;
		else if (kind === 'contact') body.assignee_contact_id = Number(ident);
		const res = await fetch(`${apiUrl()}/tasks`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { created: true };
	},

	complete: async ({ request, locals, fetch }) => {
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No task specified.' });
		const res = await fetch(`${apiUrl()}/tasks/${uid}/complete`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	cancel: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'tasks:write')) {
			return fail(403, { error: 'You do not have permission to cancel tasks.' });
		}
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No task specified.' });
		const res = await fetch(`${apiUrl()}/tasks/${uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ status: 'cancelled' })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
