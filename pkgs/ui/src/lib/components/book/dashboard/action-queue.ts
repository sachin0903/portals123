/**
 * The dashboard rail's "needs you" list.
 *
 * Two objects land here, and they are different kinds of work so they must not look alike:
 *
 *  - an **onboarding journey assigned to the caller** (`GET /applications?mine=1`). The row says
 *    who is being onboarded, where the journey has reached, and — the thing the old rail could
 *    not — whose move it is: the client's, while a step assigned to them is still open, or the
 *    adviser's. The comparison that decides "mine" happens in the API (`auth.uid()`), because the
 *    portal knows the caller by their WorkOS id and the assignee by their internal user id.
 *  - a **peer review awaiting this reviewer** (`GET /reviews?scope=inbox`). The row names the case,
 *    the change and who it concerns — more than "Peer review · a name", which is all the old rail
 *    said.
 *
 * Document requests used to be a third source. The firm-wide requests queue now sits behind
 * `documents:show`, off by default, and a request row could only be chased, so the rail no longer
 * carries them.
 */
import { ownerKind, type AppExecutionRow, type OwnerKind } from '../journeys/applications.js';
import {
	reviewCaseRef,
	reviewChangeTitle,
	reviewSubject,
	reviewWaiting,
	type ReviewRow
} from '../reviews/index.js';

export type RailKind = 'journey' | 'review';

/** Where a row opens. Injected because the same rail is mounted by the platform at `/` and by
 *  admin's operate-as-firm view under `/tenants/{uid}/operate`. */
export type RailLinks = {
	journey: (uid: string) => string;
	review: (uid: string) => string;
};

export const DEFAULT_RAIL_LINKS: RailLinks = {
	journey: (uid) => `/journeys/${uid}`,
	review: (uid) => `/reviews?open=${uid}`
};

export type RailItem = {
	/** Stable, kind-prefixed: a journey and a review could share a uid space one day. */
	key: string;
	kind: RailKind;
	/** The client being onboarded, or the change under review. */
	title: string;
	/** "Onboarding · Applicant Details · 3 of 8", or "Mr Max Oakden · GIA-00421". */
	detail: string;
	/** Whose move a journey is. Null for a review — a review is always the reviewer's own. */
	ball: OwnerKind | null;
	href: string;
	/** Sort key: work that needs the reader above a journey sitting with the client. */
	rank: number;
	started: string;
};

/** The stage words the rail prints, from the application's projected status. The journeys board
 *  has the fuller vocabulary; the rail only needs the phase a row is in. */
const STAGE: Record<string, string> = {
	draft: 'Draft',
	fact_find: 'Applicant details',
	identity_under_review: 'Identity',
	aml_under_review: 'AML',
	offshore_under_review: 'Offshore',
	terms: 'Terms',
	processing: 'Account setup',
	sync_failed: 'Account setup'
};

/** "3 of 8" once a journey has steps, nothing before it does. */
function progress(row: AppExecutionRow): string | null {
	return row.steps_total > 0 ? `${row.steps_done} of ${row.steps_total}` : null;
}

/**
 * The caller's own LIVE onboarding journeys. A completed or cancelled one is not work, and a
 * journey of some other flow is not onboarding — the rail is one question, "who am I onboarding".
 */
export function journeyItems(
	rows: AppExecutionRow[],
	links: RailLinks = DEFAULT_RAIL_LINKS
): RailItem[] {
	return rows
		.filter(
			(r) =>
				r.status !== 'completed' &&
				r.status !== 'cancelled' &&
				(r.flow_code ? r.flow_code === 'onboarding' : r.flow_name === 'Onboarding')
		)
		.map((r) => {
			const ball = ownerKind(r);
			return {
				key: `journey:${r.uid}`,
				kind: 'journey' as const,
				title: r.client_names?.trim() || 'New client',
				detail: [
					r.flow_name ?? 'Onboarding',
					STAGE[r.application_status ?? ''] ?? null,
					progress(r)
				]
					.filter(Boolean)
					.join(' · '),
				ball,
				href: links.journey(r.uid),
				rank: ball === 'client' ? 1 : 0,
				started: r.created_at
			};
		});
}

/** Reviews awaiting the signed-in reviewer. The Reviews page is the firm's whole queue; this is
 *  the slice that is the caller's to decide. The row says how long the case has waited — a day
 *  at the gate is the thing the rail exists to surface. */
export function reviewItems(
	reviews: ReviewRow[],
	links: RailLinks = DEFAULT_RAIL_LINKS,
	now: number = Date.now()
): RailItem[] {
	return reviews
		.filter((r) => r.status === 'pending')
		.map((r) => {
			const waited = reviewWaiting(r, now);
			return {
				key: `review:${r.uid}`,
				kind: 'review' as const,
				title: `${reviewCaseRef(r.case_no)} · ${reviewChangeTitle(r)}`,
				detail: [reviewSubject(r), r.account_name, `waiting ${waited.label}`]
					.filter(Boolean)
					.join(' · '),
				ball: null,
				href: links.review(r.uid),
				rank: 0,
				started: r.proposed_at
			};
		});
}

/** Work that needs the reader first; within that, the oldest first — a journey started in March has
 *  waited longer than a review raised yesterday, and burying it under newer items is how a list
 *  teaches you to ignore it. */
export function byNeed(a: RailItem, b: RailItem): number {
	if (a.rank !== b.rank) return a.rank - b.rank;
	return a.started < b.started ? -1 : a.started > b.started ? 1 : 0;
}

/** The whole rail, sorted. */
export function railItems(
	journeys: AppExecutionRow[],
	reviews: ReviewRow[],
	links: RailLinks = DEFAULT_RAIL_LINKS,
	now: number = Date.now()
): RailItem[] {
	return [...journeyItems(journeys, links), ...reviewItems(reviews, links, now)].sort(byNeed);
}
