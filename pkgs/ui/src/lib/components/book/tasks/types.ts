/**
 * Data shapes the shared task surfaces read (structural — an app's richer API types are
 * assignable). The row vocabulary itself lives in ./tasks.ts; these are the surfaces' inputs.
 */

/** One audit event on a task, as the API's `/tasks/{uid}/emails` returns it. */
export type TaskActivity = {
	id: number;
	created_at: string;
	action: string;
	detail: Record<string, unknown> | null;
	actor_email: string | null;
	actor_name: string | null;
};

/** A staff member or team a task can be handed to, already in picker shape
 *  (`user:{uuid}` / `role:{slug}` — the same portable value the flow editor uses). */
export type AssigneeOption = { value: string; label: string };

/** A staff member offered by the create dialog's assignee picker. */
export type TaskMemberOption = { user_id: string; email: string; name: string | null };

/** A team (role) offered by the create dialog's assignee picker. */
export type TaskRoleOption = { slug: string; name: string };

/** A client offered by the create dialog's assignee picker. */
export type TaskClientOption = { contact_id: number; name: string | null; email: string | null };

/** Every href the inbox renders — injected, so the admin portal can mount the same
 *  component over its own routes. */
export type TasksListingLinks = {
	/** The task's own detail page. */
	task: (uid: string) => string;
	/** The journey (flow execution) that raised a task. */
	journey: (executionId: string) => string;
	/** A client record, by contact UID. Optional: a mount that has no client record to point at
	 *  simply omits it and the names stay plain text, rather than rendering a broken href. */
	client?: (uid: string) => string;
};

/** Every href the task detail renders. */
export type TaskDetailLinks = {
	/** Back to the inbox. */
	tasks: () => string;
	/** The journey (flow execution) that raised the task. */
	journey: (executionId: string) => string;
	/** A client record, by contact UID — optional, for the reason above. */
	client?: (uid: string) => string;
};
