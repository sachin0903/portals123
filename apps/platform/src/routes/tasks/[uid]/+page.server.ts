import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { AssigneeOption, TaskActivity, TaskRow } from '@graphene/ui/book/tasks';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'tasks:read')) {
		error(403, 'You do not have permission to view tasks.');
	}
	const canManage = can(locals.permissions, locals.role, 'tasks:write');
	return {
		canManage,
		record: loadRecord(locals.accessToken, fetch, params.uid, canManage)
	};
};

async function loadRecord(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string,
	canManage: boolean
) {
	const h = authHeaders(accessToken);
	// The reassign picker needs real people/teams. Without members:read a manager just gets teams,
	// so the control degrades rather than disappearing.
	const [task, activity, roles, members] = await Promise.all([
		fetchJson<TaskRow>(fetch, `${apiUrl()}/tasks/${uid}`, h),
		fetchJson<TaskActivity[]>(fetch, `${apiUrl()}/tasks/${uid}/activity`, h),
		canManage
			? fetchJson<{ slug: string; name: string; portal: string | null }[]>(
					fetch,
					`${apiUrl()}/firm/roles`,
					h
				)
			: Promise.resolve(null),
		canManage
			? fetchJson<{ user_id: string; email: string; name: string | null }[]>(
					fetch,
					`${apiUrl()}/firm/members?kind=staff`,
					h
				)
			: Promise.resolve(null)
	]);
	if (!task.ok) {
		if (task.status === 404) error(404, 'Task not found');
		error(task.status, 'Could not load the task.');
	}
	const roleOptions: AssigneeOption[] = (roles?.ok ? roles.data : [])
		.filter((r) => r.portal !== 'client')
		.map((r) => ({ value: `role:${r.slug}`, label: `Team · ${r.name}` }));
	const userOptions: AssigneeOption[] = (members?.ok ? members.data : []).map((m) => ({
		value: `user:${m.user_id}`,
		label: `${m.name ?? m.email}`
	}));
	return {
		task: task.data,
		activity: activity.ok ? activity.data : [],
		assignees: [...userOptions, ...roleOptions]
	};
}

export const actions: Actions = {
	complete: async ({ params, locals, fetch }) => {
		const res = await fetch(`${apiUrl()}/tasks/${params.uid}/complete`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},
	cancel: async ({ params, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'tasks:write')) {
			return fail(403, { error: 'You do not have permission to cancel tasks.' });
		}
		const res = await fetch(`${apiUrl()}/tasks/${params.uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ status: 'cancelled' })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Pick the task up — `in_progress` was unreachable for a user, which made the inbox's "Mine"
	 *  tab thinner than intended. */
	start: async ({ params, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'tasks:write')) {
			return fail(403, { error: 'You do not have permission to update tasks.' });
		}
		const res = await fetch(`${apiUrl()}/tasks/${params.uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ status: 'in_progress' })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Edit the task, and/or hand it to someone else. Reassignment clears the notification stamps
	 *  server-side, so the NEW assignee is actually emailed. */
	edit: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'tasks:write')) {
			return fail(403, { error: 'You do not have permission to update tasks.' });
		}
		const form = await request.formData();
		const body: Record<string, unknown> = {};
		const title = String(form.get('title') ?? '').trim();
		if (title) body.title = title;
		const description = form.get('description');
		if (description !== null) body.description = String(description).trim() || null;
		const priority = String(form.get('priority') ?? '').trim();
		if (priority) body.priority = priority;
		const dueDate = String(form.get('due_date') ?? '').trim();
		if (dueDate) body.due_date = dueDate;
		// "user:{uuid}" | "role:{slug}" — the same portable shape the flow editor uses.
		const assignee = String(form.get('assignee') ?? '').trim();
		if (assignee) {
			const [kind, ...rest] = assignee.split(':');
			const ident = rest.join(':');
			if (kind === 'user' && ident) {
				body.assignee_kind = 'user';
				body.assignee_user_id = ident;
			} else if (kind === 'role' && ident) {
				body.assignee_kind = 'role';
				body.assignee_role_slug = ident;
			} else {
				return fail(400, { error: 'Choose who this task is for.' });
			}
		}
		if (Object.keys(body).length === 0) return fail(400, { error: 'Nothing to change.' });
		const res = await fetch(`${apiUrl()}/tasks/${params.uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
