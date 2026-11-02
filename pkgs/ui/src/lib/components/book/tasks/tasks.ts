/** Standalone tasks (docs/tasks.md) — row shape + display helpers for the platform surfaces. */

export type TaskRow = {
	id: number;
	uid: string;
	subject_type: 'contact' | 'account' | 'execution' | 'document' | null;
	subject_id: string | null;
	source_kind: 'manual' | 'flow' | 'document' | 'system';
	source_ref: string | null;
	assignee_kind: 'user' | 'role' | 'contact';
	assignee_user_id: string | null;
	assignee_role_id: number | null;
	assignee_contact_id: number | null;
	assignee_name: string | null;
	/** The assignee contact's UID — what a client-record href is built from. The serial above
	 *  cannot address `/clients/[uid]`, which is why the name rendered as dead text for years.
	 *  Optional because rows are structural: a narrower caller that never links may omit it. */
	assignee_contact_uid?: string | null;
	/** The UID of the contact a task is ABOUT (`subject_type === 'contact'`), for the same
	 *  reason — `subject_id` is the serial, and polymorphic text besides. */
	subject_contact_uid?: string | null;
	/** That contact's display name. Without it a subject link would have had no text to be: the
	 *  record showed a task's journey but never said which client it was about. */
	subject_contact_name?: string | null;
	category: 'review' | 'acknowledge' | 'upload' | 'follow_up' | 'generic';
	title: string;
	description: string | null;
	due_date: string | null;
	priority: 'low' | 'normal' | 'high';
	status: 'open' | 'in_progress' | 'done' | 'cancelled';
	created_at: string;
	completed_at: string | null;
};

export const CATEGORY_LABELS: Record<TaskRow['category'], string> = {
	review: 'Review',
	acknowledge: 'Acknowledge',
	upload: 'Upload',
	follow_up: 'Follow up',
	generic: 'Task'
};

export const SOURCE_KIND_LABELS: Record<TaskRow['source_kind'], string> = {
	manual: 'Manual',
	flow: 'Journey',
	document: 'Document',
	system: 'System'
};

/** WHOSE tasks a listing is showing. `me` is the API's own `assignee=me` — assigned to the caller
 *  OR to a role they hold, which is why the label says "mine" rather than naming a person: a task
 *  raised against the Compliance role is every compliance officer's to do.
 *
 *  It is a SERVER filter, never a slice of the rows in hand. The inbox pages, so narrowing the
 *  window client-side answers "you have nothing" when it means "nothing of yours is in the newest
 *  50" — which is how a scoped view stops being trusted. */
export type TaskScope = 'me' | 'all';

/** What the scope control says, and what an empty inbox under it means. */
export const TASK_SCOPE_LABELS: Record<TaskScope, string> = {
	me: 'Assigned to me',
	all: 'Everyone'
};

export const ASSIGNEE_KIND_LABELS: Record<TaskRow['assignee_kind'], string> = {
	user: 'Staff',
	role: 'Team',
	contact: 'Client'
};

/** The slice of a task the display rules below actually read. Structural on purpose: the admin
 *  portal's client record shows tasks through a narrower row (uid/title/due_date/status — see
 *  `TaskDisplayRow`), and typing these on the full `TaskRow` forced it to keep a copy of the
 *  rules rather than the rules' inputs. */
export type TaskDisplayRow = Pick<TaskRow, 'due_date' | 'status'>;

export function isOpen(t: Pick<TaskRow, 'status'>): boolean {
	return t.status === 'open' || t.status === 'in_progress';
}

/** Today as a bare ISO date in **UTC** — the same calendar day the API's `CURRENT_DATE` uses.
 *
 *  `due_date` is a bare date (no time, no zone). Parsing it into a local `Date` and comparing
 *  against `new Date()` made overdue-ness a function of the reader's timezone: the same task read as
 *  overdue in Sydney and not in London, and both could disagree with the API's own near-due scan. */
export function todayIso(now: Date = new Date()): string {
	return now.toISOString().slice(0, 10);
}

/** Whole days from `today` to a bare due date (negative ⇒ in the past). Date-only arithmetic, so no
 *  DST or timezone offset can shift the answer by a day. */
function daysUntil(dueDate: string, today: string): number {
	return Math.round(
		(Date.parse(`${dueDate}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000
	);
}

export function isOverdue(t: TaskDisplayRow, today: string = todayIso()): boolean {
	if (!t.due_date || !isOpen(t)) return false;
	// Due TODAY is not yet overdue — the person has the day to do it.
	return t.due_date < today;
}

/** "Due today" / "Due in 3 days" / "4 days overdue" / "" — compact due phrasing for a row. */
export function dueLabel(t: TaskDisplayRow, today: string = todayIso()): string {
	if (!t.due_date) return '';
	const days = daysUntil(t.due_date, today);
	if (days === 0) return 'Due today';
	if (days === 1) return 'Due tomorrow';
	if (days > 1) return `Due in ${days} days`;
	const late = Math.abs(days);
	return `${late} ${late === 1 ? 'day' : 'days'} overdue`;
}

/** Sort open work by urgency: overdue first, then nearest due date, then newest. */
export function byUrgency(
	a: TaskDisplayRow & Pick<TaskRow, 'id'>,
	b: TaskDisplayRow & Pick<TaskRow, 'id'>,
	today: string = todayIso()
): number {
	const ao = isOverdue(a, today) ? 0 : 1;
	const bo = isOverdue(b, today) ? 0 : 1;
	if (ao !== bo) return ao - bo;
	if (a.due_date && b.due_date && a.due_date !== b.due_date)
		return a.due_date < b.due_date ? -1 : 1;
	if (!!a.due_date !== !!b.due_date) return a.due_date ? -1 : 1;
	return b.id - a.id;
}
