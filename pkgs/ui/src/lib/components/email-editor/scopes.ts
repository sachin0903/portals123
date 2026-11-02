/** WHAT AN EVENT CAN TELL AN EMAIL — the details a send carries, by what the email is ABOUT.
 *
 *  An email only ever talks about the one thing that happened, so the facts it can carry are the
 *  event's SUBJECT plus a few constants. That makes this list short and closed, which is why it can be
 *  a list at all: the alternative (a query per fact) was designed and rejected as over-built.
 *
 *  Mirrors `app/notifications/context.py::SCOPE_FIELDS`; `scope-parity.test.ts` reads that Python
 *  source, so a field added or renamed there fails here rather than drifting quietly.
 */

/** The subject kinds a trigger can have. `null` = available whatever the event is about. */
export type SubjectKind = 'contact' | 'account' | 'task' | 'document';

export interface ScopeField {
	/** The dotted path the notification context resolves it under. */
	path: string;
	/** The author's words for it. */
	label: string;
}

const F = (path: string, label: string): ScopeField => ({ path, label });

/** Always available — these do not depend on what the event is about. */
export const ALWAYS: readonly ScopeField[] = [
	F('firm.name', 'Firm name'),
	F('firm.phone', 'Firm phone'),
	F('actor.name', 'Who did it'),
	F('actor.email', 'Who did it — email'),
	F('event.when', 'When it happened')
];

const PERSON: readonly ScopeField[] = [
	F('client.name', 'Client name'),
	F('client.first_name', 'Client first name'),
	F('adviser.name', 'Adviser name'),
	F('adviser.email', 'Adviser email')
];

/** Per subject kind. The person fields repeat because a contact is reachable from each of them. */
export const BY_SUBJECT: Readonly<Record<SubjectKind, readonly ScopeField[]>> = {
	contact: PERSON,
	account: [
		F('account.name', 'Account name'),
		F('account.number', 'Account number'),
		F('account.product', 'Product'),
		F('account.status', 'Account status'),
		F('account.value', 'Account value'),
		F('account.currency', 'Currency'),
		...PERSON
	],
	task: [
		F('task.title', 'Task title'),
		F('task.description', 'Task description'),
		F('task.due_date', 'Task due date')
	],
	document: [F('document.name', 'Document name'), F('document.type', 'Document type'), ...PERSON]
};

export const SUBJECT_LABELS: Readonly<Record<SubjectKind, string>> = {
	contact: 'A client',
	account: 'An account',
	task: 'A task',
	document: 'A document'
};

/** The fields offered for the subjects an email says it is about. Empty selection ⇒ the constants
 *  alone, which is honest: an email that claims no subject can only talk about the firm and the act. */
export function fieldsFor(subjects: readonly SubjectKind[]): ScopeField[] {
	const seen = new Set<string>();
	const out: ScopeField[] = [];
	for (const field of [...ALWAYS, ...subjects.flatMap((s) => BY_SUBJECT[s] ?? [])]) {
		if (seen.has(field.path)) continue;
		seen.add(field.path);
		out.push(field);
	}
	return out;
}

/** Every path any subject could supply — the whole vocabulary, whatever an email is about. */
export const KNOWN_PATHS: ReadonlySet<string> = new Set(
	fieldsFor(Object.keys(BY_SUBJECT) as SubjectKind[]).map((f) => f.path)
);

/** Grouped by their scope's first segment, for a menu that reads as scopes rather than one long list. */
export function grouped(fields: readonly ScopeField[]): { group: string; fields: ScopeField[] }[] {
	const out: { group: string; fields: ScopeField[] }[] = [];
	for (const field of fields) {
		const group = field.path.split('.')[0] ?? field.path;
		const last = out.at(-1);
		if (last && last.group === group) last.fields.push(field);
		else out.push({ group, fields: [field] });
	}
	return out;
}

/** The subjects a stored schema claims. Tolerant: the column is free-form jsonb, so an unknown kind is
 *  dropped rather than shown as a claim nothing can honour. Ordered as `BY_SUBJECT` declares them, so
 *  two templates claiming the same set read the same way. */
export function readSubjects(schema: unknown): SubjectKind[] {
	const raw = (schema as { subjects?: unknown } | null | undefined)?.subjects;
	if (!Array.isArray(raw)) return [];
	const wanted = new Set(raw.map((k) => String(k).trim()));
	return (Object.keys(BY_SUBJECT) as SubjectKind[]).filter((k) => wanted.has(k));
}
