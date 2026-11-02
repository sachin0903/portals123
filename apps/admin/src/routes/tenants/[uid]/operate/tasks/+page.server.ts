import { error, fail, redirect } from '@sveltejs/kit';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import type { TaskRow } from '@graphene/ui/book/tasks';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url, locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	// Each may be given SEVERAL times (`?category=a&category=b`) — the filter bar offers several
	// and the question is an OR.
	const category = url.searchParams.getAll('category').filter(Boolean);
	// WHEN it is due, as windows rather than a boolean: overdue / today / week. `overdue=1` is
	// still read so a bookmark from before this change still means what it said.
	const due = url.searchParams.getAll('due').filter(Boolean);
	if (!due.length && url.searchParams.get('overdue') === '1') due.push('overdue');
	// Search and priority, applied by the API like the two above: the inbox holds one page and
	// keysets backwards, so narrowing the rows in hand would answer for the window, not the book.
	const q = url.searchParams.get('q')?.trim() ?? '';
	const priority = url.searchParams.getAll('priority').filter(Boolean);
	const beforeId = url.searchParams.get('before') ?? '';
	const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
	// `append`, not `set`: repeated params are how a multi-valued filter reaches the API.
	for (const value of category) params.append('category', value);
	for (const value of due) params.append('due', value);
	if (q) params.set('q', q);
	for (const value of priority) params.append('priority', value);
	if (beforeId) params.set('before_id', beforeId);
	const mineParams = new URLSearchParams(params);
	mineParams.set('assignee', 'me');
	const [all, mine] = await Promise.all([
		targetJson<TaskRow[]>(fetch, locals.accessToken, tenant.uid, '/tasks', params),
		targetJson<TaskRow[]>(fetch, locals.accessToken, tenant.uid, '/tasks', mineParams)
	]);
	if (!all.ok) error(all.status || 500, 'Could not load tasks.');
	return {
		tasks: all.data,
		mineUids: new Set((mine?.ok ? mine.data : []).map((task) => task.uid)),
		nextBefore: all.data.length === PAGE_SIZE ? Math.min(...all.data.map((task) => task.id)) : null,
		filters: { category, due, q, priority },
		writing
	};
};

async function mutate(
	event: Parameters<NonNullable<Actions[keyof Actions]>>[0],
	path: string,
	body: unknown,
	method = 'POST'
) {
	if (!canOperateWrite(event.locals)) return readOnlyFailure();
	const response = await targetMutation(
		event.fetch,
		event.locals.accessToken,
		event.params.uid,
		path,
		{
			method,
			body: JSON.stringify(body)
		}
	);
	if (!response.ok) return fail(response.status, { error: await detail(response) });
	return { ok: true };
}

export const actions: Actions = {
	create: async (event) => {
		const form = await event.request.formData();
		const title = String(form.get('title') ?? '').trim();
		const [kind, ...rest] = String(form.get('assignee') ?? '').split(':');
		const ident = rest.join(':');
		if (!title || !kind || !ident) return fail(400, { error: 'A title and assignee are needed.' });
		return mutate(event, '/tasks', {
			title,
			assignee_kind: kind,
			...(kind === 'user' ? { assignee_user_id: ident } : {}),
			...(kind === 'role' ? { assignee_role_slug: ident } : {}),
			...(kind === 'contact' ? { assignee_contact_id: Number(ident) } : {}),
			category: String(form.get('category') ?? 'generic'),
			priority: String(form.get('priority') ?? 'normal'),
			description: String(form.get('description') ?? '').trim() || null,
			due_date: String(form.get('due_date') ?? '') || null
		});
	},
	complete: async (event) => {
		const uid = String((await event.request.formData()).get('uid') ?? '');
		return mutate(event, `/tasks/${uid}/complete`, {});
	},
	cancel: async (event) => {
		const uid = String((await event.request.formData()).get('uid') ?? '');
		return mutate(event, `/tasks/${uid}`, { status: 'cancelled' }, 'PATCH');
	}
};
