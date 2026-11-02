// The grouping behind the compact EventFeed: collapse a burst of VIEW events — the SAME event type,
// actor and primary subject within a 30-minute window — into one row with a count, so a client
// opening a document forty times reads as one "Viewed … (40)" line rather than forty.
//
// ONLY views collapse. A view is a passive, high-frequency read that floods a glance; a send, a
// generation, an upload, an archive is a DISTINCT act, and forty of them are forty real events that
// each deserve their own line — collapsing them would hide work. So a non-view event never joins a
// group, even if an identical-looking one sits beside it in the window.

import type { ActionEvent } from './describe.js';

/** A view/read event — the only kind that collapses. Keyed on the operation verb in the event type
 *  (`document.viewed`); everything else stays discrete. */
function isCollapsible(e: ActionEvent): boolean {
	// BOTH spellings. `audit.actions` is append-only, so a document's activity is mostly rows written
	// before the `resource.verb` rename — and this is the one place where missing them is invisible
	// rather than wrong: the events still render, they just stop collapsing, so an old document's rail
	// reads as forty separate "viewed a document" lines where a new one reads as one row saying 40.
	return e.event_type.endsWith('.viewed') || e.event_type.includes('-view-');
}

/** A run of collapsed events: the newest one is the representative, `members` holds the rest. */
export interface GroupedActionEvent {
	event: ActionEvent;
	count: number;
	members: ActionEvent[];
}

const GROUP_WINDOW_MS = 30 * 60 * 1000;

/** The subject a group keys on — the document/entity the events are about, so two clients viewing
 *  the SAME document collapse but views of DIFFERENT documents don't. `uid` (documents/shares) then
 *  `id` (record routes) then the first resolved entity; '' when the event names nothing. */
function primaryId(e: ActionEvent): string {
	const pp = (e.path_params ?? {}) as Record<string, unknown>;
	const raw = pp.uid ?? pp.id;
	if (raw != null) return String(raw);
	const ent = e.entities?.[0];
	return ent ? `${ent.kind}:${ent.id}` : '';
}

function groupKey(e: ActionEvent): string {
	return `${e.event_type}|${e.actor_sub ?? ''}|${primaryId(e)}`;
}

function whenMs(e: ActionEvent): number {
	return new Date(e.created_at).getTime();
}

/**
 * Collapse repeated VIEW events into count-bearing rows. Input is the stream's own order — newest
 * first (id descending) — and the output preserves it: a new group appears where its newest member
 * sits. A view joins the open group for its key only while it stays within `windowMs` of that group's
 * OLDEST-so-far member (the views chain), so a long steady trickle keeps collapsing while a gap
 * starts a fresh row. An unparseable timestamp never chains (its distance is NaN, which fails the
 * `<=` test), so a bad row lands on its own rather than swallowing the group. A non-view event is
 * always its own row (count 1) — it never joins or opens a group.
 */
export function groupEvents(
	events: ActionEvent[],
	windowMs = GROUP_WINDOW_MS
): GroupedActionEvent[] {
	const groups: GroupedActionEvent[] = [];
	const openByKey = new Map<string, GroupedActionEvent>();
	for (const e of events) {
		if (!isCollapsible(e)) {
			// A distinct act: its own row, and it never becomes a chain target for a later view.
			groups.push({ event: e, count: 1, members: [e] });
			continue;
		}
		const key = groupKey(e);
		const open = openByKey.get(key);
		const oldest = open ? open.members[open.members.length - 1] : null;
		if (open && oldest && Math.abs(whenMs(oldest) - whenMs(e)) <= windowMs) {
			open.members.push(e);
			open.count += 1;
		} else {
			const group: GroupedActionEvent = { event: e, count: 1, members: [e] };
			groups.push(group);
			openByKey.set(key, group);
		}
	}
	return groups;
}
