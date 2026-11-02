/**
 * Pure read helpers for the action-event stream's detail view — the logic behind the expanded
 * panel (the changes list, entity rows, permission deltas) and the row headline. Extracted from
 * the component so it is unit-tested in its own right and shared by the row + detail-pane
 * components of the master-detail Events surface. Every function is a pure function of one
 * `ActionEvent` (plus `describe.ts`'s own pure helpers); nothing here touches component state.
 */
import { previewValue } from './format-value.js';
import {
	actorLabel,
	actorLedSentence,
	changedFields,
	deslug,
	describeActionEvent,
	fieldWord,
	permissionsDiff,
	subjectEntityKind,
	type ActionEntity,
	type ActionEvent
} from './describe.js';

/** The house initials-disc derivation (adviser-strip): first letter of each word-ish chunk,
 *  first two, uppercased — emails split on dots/@ so "m.cole@…" reads MC. */
export function initialsOf(name: string): string {
	return name
		.split(/[\s._@-]+/)
		.filter(Boolean)
		.map((w) => w[0] ?? '')
		.join('')
		.slice(0, 2)
		.toUpperCase();
}

/** The Discord-style headline, split so the actor renders bold and the rest quiet:
 *  "Alice Feed" + " changed m.cole's platform role: Adviser → Compliance". */
export function headline(e: ActionEvent): { actor: string; rest: string } {
	const actor = actorLabel(e);
	const sentence = describeActionEvent(
		e.event_type,
		e.path_params,
		e.entities ?? null,
		e.metadata ?? null
	);
	return { actor, rest: actorLedSentence(actor, sentence).slice(actor.length) };
}

/** The read-time id → label resolution the API attached, or [] (older responses omit it). */
export function entitiesOf(e: ActionEvent): ActionEntity[] {
	return Array.isArray(e.entities) ? e.entities : [];
}

/** The ids the entity rows already represent — used to keep the raw param/change rows from
 *  repeating what the rows say better. */
export function coveredIds(e: ActionEvent): Set<string> {
	return new Set(entitiesOf(e).map((ent) => ent.id));
}

/** Path params NOT represented by an entity row (unresolvable kinds, non-id params like
 *  `portal`) — they keep the plain chip treatment. */
export function residualPathParams(e: ActionEvent): [string, unknown][] {
	const covered = coveredIds(e);
	return Object.entries(e.path_params ?? {}).filter(([, v]) => !covered.has(String(v)));
}

/** The stored placeholder for a side of a change that held nothing. Rendered rather than dropped,
 *  because "(not set) → Balanced" says something "Balanced" does not: that the field was EMPTY,
 *  which is the difference between a correction and a first answer. */
export const NOT_SET = '(not set)';

/** The reserved key `app/audit_actions.py::diff_rows` records an overflowing tail under. */
const OMITTED = '_omitted';

/**
 * The columns an update actually MOVED — `metadata.changes`, written by `app/audit_actions.py` as the
 * difference between two reads of the row (`{column: {from, to}}`). The only capture that can
 * answer "what was it before": `metadata.changed` gives the names a request carried and
 * `metadata.body` the values it asked for, and neither is the OUTCOME.
 */
export function recordedChanges(e: ActionEvent): [string, unknown, unknown][] {
	const raw = (e.metadata ?? {}).changes;
	if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return [];
	const out: [string, unknown, unknown][] = [];
	for (const [column, pair] of Object.entries(raw as Record<string, unknown>)) {
		if (!pair || typeof pair !== 'object' || Array.isArray(pair)) continue;
		const { from = null, to = null } = pair as { from?: unknown; to?: unknown };
		out.push([column, from, to]);
	}
	return out;
}

export function metadataRecord(
	e: ActionEvent,
	key: 'subject' | 'previous'
): Record<string, unknown> {
	const value = (e.metadata ?? {})[key];
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}

/** The metadata keys the pane states SOMEWHERE ELSE, so Details never repeats them. `query` and
 *  `correlation_id` have their own Metadata rows; `subject`/`previous` drive the change rows. */
const SHOWN_ELSEWHERE = new Set([
	'correlation_id',
	'query',
	'subject',
	'previous',
	// `changed` has its own row below — spelled out as words rather than previewed as a JSON list.
	'changed',
	// `body` is rendered field by field (`bodyFields`), not previewed as one blob.
	'body',
	// `changes` drives the change rows — old → new, one row per column that moved.
	'changes'
]);

/** DETAILS: what the event carries that nothing above has already said, as readable rows.
 *
 *  This is the PARSED replacement for the raw-JSON block. That block was the only place a custom
 *  metadata key — or a subject field no change row covered — could be read at all, so removing it
 *  without this left an authentication event showing a headline and four rows of chrome and
 *  nothing about the event itself. Parsed, because a wall of line-numbered JSON is not what an
 *  adviser or a compliance reader opens an event for.
 *
 *  Objects and lists are PREVIEWED rather than expanded: `previewValue` inlines a small one and
 *  names the shape of a big one, so a 40-field blob states its size instead of burying the rows
 *  around it.
 */
/**
 * The recorded request body, field by field (`metadata.body`, api `BODY_CAPTURE_EVENTS`) — what
 * makes an act whose content is free text or a form's answers readable at all. Labelled facts
 * rather than JSON: a reader wants "Surname: Cole", not a blob. Nested values are PREVIEWED, so a
 * form's forty answers state their size instead of burying the fields around them. A `[redacted]`
 * value is left as the API wrote it.
 */
export function bodyFields(e: ActionEvent): [string, string][] {
	const body = (e.metadata ?? {}).body;
	if (!body || typeof body !== 'object' || Array.isArray(body)) return [];
	// NOTHING THE PANEL ALREADY STATES. Most of a request is already on the row by a better route:
	// the captured SUBJECT renders as change rows and a resolved id as a NAMED chip. Printed again
	// from the body they came back as a second copy and a raw id — one `document.generated` showed
	// "Idempotency key" twice and `client_contact_id` as a bare `1` beside the client it had named.
	const subject = metadataRecord(e, 'subject');
	const covered = coveredIds(e);
	// ...AND NOTHING THE RECORDED DIFF SAYS BETTER: a change row gives the same fact with its
	// before-value attached. Matched on the NAME where the two agree, on the name the column
	// SUFFIXES where they do not (the wire sends `notes`, the column is `vulnerability_notes`), and
	// otherwise on the value — excluding booleans and 0/1, which collide across unrelated fields.
	const changed = recordedChanges(e);
	// A JSONB column's SUBFIELDS are change rows of their own (see `changeRows`), so the names that
	// have already been spoken for include them - without this a bank account prints every field
	// twice, once as "(not set) -> ***6352" and once as a bare body value.
	const columns = changed.flatMap(([column, from, to]) => {
		const pair = asObjects(from, to);
		return pair
			? [column, ...new Set([...Object.keys(pair[0]), ...Object.keys(pair[1])])]
			: [column];
	});
	const newValues = new Set(changed.map(([, , to]) => JSON.stringify(to ?? null)));
	const alreadyAChangeRow = (key: string, value: unknown) =>
		columns.includes(key) ||
		columns.some((column) => column.endsWith(`_${key}`)) ||
		(typeof value !== 'boolean' &&
			value !== 0 &&
			value !== 1 &&
			newValues.has(JSON.stringify(value)));
	return Object.entries(body as Record<string, unknown>)
		.filter(([key, value]) => {
			if (value === null || value === undefined) return false;
			if (key in subject) return false;
			if (alreadyAChangeRow(key, value)) return false;
			return !covered.has(String(value));
		})
		.map(([key, value]) => [fieldLabel(key), previewValue(value)]);
}

export function detailRows(e: ActionEvent): [string, string][] {
	const rows: [string, string][] = [];
	// WHICH fields moved, in full: the headline names at most three, and "what else did that save
	// touch" is the question this pane is opened to answer.
	//
	// SUPPRESSED where something already says it BETTER. The body names the same fields and gives
	// their values; the change rows name them with both sides. Listing them a second time as bare
	// words is a line of noise per event - and when the only source is `metadata.changed` it is a
	// WRONG line, because that is what the request carried rather than what it altered.
	const changed =
		bodyFields(e).length > 0 || recordedChanges(e).length > 0 ? [] : changedFields(e.metadata);
	if (changed.length > 0) {
		rows.push([
			changed.length === 1 ? 'Field changed' : 'Fields changed',
			changed.map(fieldWord).join(', ')
		]);
	}
	// Anything the endpoint chose to record that no row above states. Unknown BY DEFINITION — an
	// allowlist here would hide the next key somebody adds, which is exactly how the raw JSON came
	// to be load-bearing. `subject` and `previous` are excluded because `changeRows` already reads
	// them, as values or as "old to new".
	for (const [key, value] of Object.entries(e.metadata ?? {})) {
		if (SHOWN_ELSEWHERE.has(key) || value === null || value === undefined) continue;
		rows.push([fieldLabel(key), previewValue(value)]);
	}
	return rows;
}

/** The permissions delta when BOTH sides were captured — rendered as +added / −removed chips
 *  (the only-the-change principle: never the full list). */
export function permissionsDiffOf(e: ActionEvent) {
	return permissionsDiff(
		metadataRecord(e, 'previous')['permissions'],
		metadataRecord(e, 'subject')['permissions']
	);
}

/** Rows written before PREVIOUS_FIELDS landed have no diff to show — collapse the captured list
 *  to a COUNT (the raw JSON toggle keeps the full list for engineers), never dump it. */
export function legacyPermissionsCount(e: ActionEvent): number | null {
	if (permissionsDiffOf(e)) return null;
	const list = metadataRecord(e, 'subject')['permissions'];
	return Array.isArray(list) ? list.length : null;
}

export interface ChangeRow {
	label: string;
	/** null ⇒ nothing captured on that side (a set with no previous, a clear with no new). */
	from: string | null;
	to: string | null;
}

/** `role_slug` → "Role", `model_id` → "Model", `capacity` → "Capacity" — captured field names as
 *  row labels (id/slug suffixes are plumbing, not prose). */
export function fieldLabel(field: string): string {
	// Adjacent REPEATS collapse: `client_contact_id` loses its `_id` and `deslug` maps the schema's
	// `contact` to the product's `client`, so the label read "Client client".
	const words = deslug(field.replace(/_(id|ids|uid|slug)$/, '')).split(' ');
	return words
		.filter((word, i) => word.toLowerCase() !== (words[i - 1] ?? '').toLowerCase())
		.join(' ');
}

/** A captured value as the reader should see it: resolved entity label when the id resolves,
 *  de-slugged for slug fields, and lists collapsed to a count (only-the-change: a 30-strong list
 *  is never prose — the raw toggle keeps it). */
export function displayValue(e: ActionEvent, field: string, value: unknown): string {
	if (Array.isArray(value)) return `${value.length} ${fieldLabel(field).toLowerCase()}`;
	const ent = entitiesOf(e).find((x) => x.id === String(value));
	if (ent?.label) return ent.label;
	if (field.endsWith('_slug') || field === 'slug') return deslug(String(value));
	// An object has no sensible `String()` - it reads as "[object Object]", which is the shape of a
	// value rendered by something that did not know what it was holding. `previewValue` inlines a
	// small one and names the shape of a big one.
	if (value !== null && typeof value === 'object') return previewValue(value);
	return String(value);
}

/** One row per changed property, Discord-style: previous+subject pairs read "old → new";
 *  subject-only fields read as plain values (skipped when an entity row already names them);
 *  previous-only fields read "was old" (the new value wasn't captured — e.g. a cleared mandate,
 *  whose null never lands in the subject). Permission lists are excluded here — they get the
 *  dedicated diff/count treatment. */
/** Both sides as plain objects, or null when NEITHER is one - the test for "this column is a
 *  document, so diff it field by field" rather than "render it as a blob".
 *
 *  An ABSENT side counts as an empty object, not as a reason to give up: a bank account set for the
 *  first time has no before, and the answer a reader wants is still one row per field
 *  ("Sort code: (not set) -> 12-34"), not the whole record pasted into one. */
function asObjects(
	from: unknown,
	to: unknown
): [Record<string, unknown>, Record<string, unknown>] | null {
	const plain = (v: unknown) => v !== null && typeof v === 'object' && !Array.isArray(v);
	const absent = (v: unknown) => v === null || v === undefined;
	if (!(plain(from) || plain(to))) return null;
	if (!(plain(from) || absent(from)) || !(plain(to) || absent(to))) return null;
	return [
		(plain(from) ? from : {}) as Record<string, unknown>,
		(plain(to) ? to : {}) as Record<string, unknown>
	];
}

export function changeRows(e: ActionEvent): ChangeRow[] {
	// THE RECORDED DIFF FIRST. It is the only source that states both sides of the same
	// fact, so where it covers a field nothing else should also speak for it — `seen`
	// keeps the subject/previous pairing below off a label this has already answered.
	const rows: ChangeRow[] = [];
	const seen = new Set<string>();
	for (const [column, from, to] of recordedChanges(e)) {
		if (column === OMITTED) {
			// The overflow count, not a column: it has no before-value and must not be given a
			// fabricated one.
			rows.push({ label: 'Also changed', from: null, to: String(to) });
			continue;
		}
		const label = fieldLabel(column);
		// A JSONB COLUMN IS NOT ONE VALUE. `bank_account` moves as a whole object, and one row for it
		// says "the bank account changed" - which the event type already said - with the record
		// pasted in as JSON beside it. The answer a reader wants is per FIELD, each with its own
		// before and after: sort code 12-34 -> 56-78, and nothing about the name that did not move.
		const pair = asObjects(from, to);
		if (pair) {
			const [was, now] = pair;
			for (const key of [...new Set([...Object.keys(was), ...Object.keys(now)])].sort()) {
				if (JSON.stringify(was[key] ?? null) === JSON.stringify(now[key] ?? null)) continue;
				// Labelled by the FIELD, not "{column}: {field}". `bank_account` is the column the
				// writer happened to use; `Sort code` is what moved, and it is also the name the
				// request body used - so this label is what keeps the body row below from saying
				// the same thing again underneath.
				const sub = fieldLabel(key);
				rows.push({
					label: sub,
					from: was[key] == null ? NOT_SET : displayValue(e, key, was[key]),
					to: now[key] == null ? NOT_SET : displayValue(e, key, now[key])
				});
				seen.add(sub);
			}
			seen.add(label);
			continue;
		}
		rows.push({
			label,
			from: from === null ? NOT_SET : displayValue(e, column, from),
			to: to === null ? NOT_SET : displayValue(e, column, to)
		});
		seen.add(label);
	}
	const prev = metadataRecord(e, 'previous');
	const subj = metadataRecord(e, 'subject');
	const labels = new Set(
		entitiesOf(e)
			.map((x) => x.label)
			.filter((l): l is string => !!l)
	);
	const covered = coveredIds(e);
	for (const key of new Set([...Object.keys(prev), ...Object.keys(subj)])) {
		if (seen.has(fieldLabel(key))) continue; // the recorded diff already said it, with both sides
		if (key === 'permissions' && (Array.isArray(prev[key]) || Array.isArray(subj[key]))) continue; // the diff / count row owns this one
		const inPrev = key in prev;
		const inSubj = key in subj;
		if (inPrev && inSubj) {
			rows.push({
				label: fieldLabel(key),
				from: displayValue(e, key, prev[key]),
				to: displayValue(e, key, subj[key])
			});
		} else if (inPrev) {
			rows.push({ label: fieldLabel(key), from: displayValue(e, key, prev[key]), to: null });
		} else {
			const value = subj[key];
			// By ID **or by the text the entity row shows**. A captured `name` holds the document's
			// title, not its uid, so an id-only test let "Document name — ID Verification" render
			// immediately above a DOCUMENT row reading "ID Verification" with a link on it: the same
			// fact twice, the second time better.
			const named = (item: unknown) =>
				covered.has(String(item)) || labels.has(displayValue(e, key, item));
			const alreadyATarget = Array.isArray(value)
				? value.length > 0 && value.every(named)
				: named(value);
			if (alreadyATarget) continue; // the Targets row says it better (label + link)
			rows.push({ label: fieldLabel(key), from: null, to: displayValue(e, key, value) });
		}
	}
	return rows;
}

/** The captured before-value ids, so a Targets row can wear a quiet "previous" pill when it names
 *  the OLD value of a change (two Model rows would otherwise be indistinguishable). */
export function previousIds(e: ActionEvent): Set<string> {
	const out = new Set<string>();
	for (const value of Object.values(metadataRecord(e, 'previous'))) {
		if (Array.isArray(value)) value.forEach((item) => out.add(String(item)));
		else if (value !== null && value !== undefined) out.add(String(value));
	}
	return out;
}

/** The ids the CURRENT state names (path params + subject) — a previous pill only makes sense
 *  when the id is *not* also part of the new state. */
export function currentIds(e: ActionEvent): Set<string> {
	const out = new Set<string>();
	for (const value of Object.values(e.path_params ?? {})) out.add(String(value));
	for (const value of Object.values(metadataRecord(e, 'subject'))) {
		if (Array.isArray(value)) value.forEach((item) => out.add(String(item)));
		else out.add(String(value));
	}
	return out;
}

/** The event's correlation id (for the engineer floor's copy control), or null. */
export function correlationId(e: ActionEvent): string | null {
	const id = (e.metadata ?? {}).correlation_id;
	return typeof id === 'string' && id ? id : null;
}

/** The captured query params (redacted at write time), as entries for the plumbing chips. */
export function queryParams(e: ActionEvent): [string, unknown][] {
	const q = (e.metadata ?? {}).query;
	return q && typeof q === 'object' ? Object.entries(q as Record<string, unknown>) : [];
}

/**
 * The entities worth offering a "go to the record" button for, SUBJECT FIRST.
 *
 * A row can resolve several entities — a document share names the document AND the client it was
 * shared with — and only one of them is what a reader means by "the relevant item": the thing the
 * verb acted on. That is the subject kind (`document.viewed` → the document), so it leads; the rest
 * follow in the order the API resolved them, as fallbacks for when the host app has no route for
 * the subject.
 *
 * Entities with no `uid` are dropped: the serial the stream RECORDED is history, not an address, so
 * there is nothing a host app could route to (see ActionEntity).
 */
export function targetEntities(e: ActionEvent): ActionEntity[] {
	const linkable = entitiesOf(e).filter((ent) => !!ent.uid);
	const subject = subjectEntityKind(e.event_type);
	if (!subject) return linkable;
	return [
		...linkable.filter((ent) => ent.kind === subject),
		...linkable.filter((ent) => ent.kind !== subject)
	];
}
