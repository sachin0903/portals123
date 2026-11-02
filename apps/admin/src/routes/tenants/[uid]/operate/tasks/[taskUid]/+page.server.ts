import { error, fail, redirect } from '@sveltejs/kit';
import type { AssigneeOption, TaskActivity, TaskRow } from '@graphene/ui/book/tasks';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type Role = { slug: string; name: string; portal: string | null };
type Member = { user_id: string; email: string; name: string | null };

export const load: PageServerLoad = async ({ params, locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [task, activity, roles, members] = await Promise.all([
		targetJson<TaskRow>(fetch, locals.accessToken, tenant.uid, `/tasks/${params.taskUid}`),
		targetJson<TaskActivity[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			`/tasks/${params.taskUid}/activity`
		),
		writing ? targetJson<Role[]>(fetch, locals.accessToken, tenant.uid, '/firm/roles') : null,
		writing ? targetJson<Member[]>(fetch, locals.accessToken, tenant.uid, '/firm/members') : null
	]);
	if (!task.ok) {
		if (task.status === 404) error(404, 'Task not found.');
		error(task.status || 500, 'Could not load the task.');
	}
	const assignees: AssigneeOption[] = [
		...(members?.ok
			? members.data.map((member) => ({
					value: `user:${member.user_id}`,
					label: member.name ?? member.email
				}))
			: []),
		...(roles?.ok
			? roles.data
					.filter((role) => role.portal !== 'client')
					.map((role) => ({ value: `role:${role.slug}`, label: `Team · ${role.name}` }))
			: [])
	];
	return {
		task: task.data,
		activity: activity.ok ? activity.data : [],
		activityFailed: !activity.ok,
		assignees,
		writing
	};
};

async function patch(
	event: Parameters<NonNullable<Actions[keyof Actions]>>[0],
	path: string,
	body: unknown,
	method = 'PATCH'
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
	complete: (event) => patch(event, `/tasks/${event.params.taskUid}/complete`, {}, 'POST'),
	start: (event) => patch(event, `/tasks/${event.params.taskUid}`, { status: 'in_progress' }),
	cancel: (event) => patch(event, `/tasks/${event.params.taskUid}`, { status: 'cancelled' }),
	edit: async (event) => {
		const form = await event.request.formData();
		const body: Record<string, unknown> = {};
		for (const field of ['title', 'description', 'priority', 'due_date']) {
			const value = form.get(field);
			if (value !== null && String(value).trim()) body[field] = String(value).trim();
		}
		const assignee = String(form.get('assignee') ?? '');
		const [kind, ...rest] = assignee.split(':');
		const ident = rest.join(':');
		if (kind === 'user' && ident)
			Object.assign(body, { assignee_kind: 'user', assignee_user_id: ident });
		if (kind === 'role' && ident)
			Object.assign(body, { assignee_kind: 'role', assignee_role_slug: ident });
		if (!Object.keys(body).length) return fail(400, { error: 'Nothing to change.' });
		return patch(event, `/tasks/${event.params.taskUid}`, body);
	}
};
