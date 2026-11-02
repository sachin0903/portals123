/**
 * The injected contracts for the shared journey surfaces (the portal waterfall: the platform
 * portal's /journeys pages and the admin portal's read-only per-tenant view mount the same
 * components over their own loaders).
 *
 * Route knowledge is injected: the shared components never hardcode an href — every link is a
 * builder the app supplies. Mutations exist only when the app passes `actions` (its own
 * form-action names plus SvelteKit's `enhance`, which pkgs/ui cannot import); absent ⇒ the
 * surface renders read-only, with every mutating control hidden.
 */
import type { Enhance } from '../clients/form-enhance.js';

export interface JourneysListingLinks {
	/** A journey's detail page — the board cards and the list rows. */
	journey: (uid: string) => string;
	/** Published flows the caller may start. Absent/empty ⇒ the action is hidden. */
	newJourneys?: { code: string; name: string; href: string }[];
	/** Branded PDF of a journey. The listing offers it only for a completed onboarding. */
	applicationPdf?: (uid: string) => string;
}

export interface JourneyDetailLinks {
	/** The journeys listing this record backs out to. */
	journeys: string;
	/** The staff form-fill page for a staff-assigned `fill_form` step. */
	fillStep: (caseUid: string, stepKey: string, participantContactId: number | null) => string;
	/** A document linked to a step (a generated pack, an uploaded file). */
	document: (uid: string) => string;
	/** Branded PDF of this journey: the questions and answers, the terms agreed, and the signature.
	 *  Optional: a mount with no download route simply has no button. */
	applicationPdf?: string;
	/** A printable copy of the declaration behind an `accept_terms` step, for the client to sign
	 *  in person — offered from the Record acceptance dialog, which needs the signed copy back.
	 *  Optional: a mount with no print route (the admin's read-only view) simply has no link. */
	termsCopy?: (caseUid: string, stepKey: string, participantContactId: number | null) => string;
	/** An applicant's client record, from the Applicants column.
	 *
	 *  A journey is where a reader first meets these people, so it is the surface they most want to
	 *  leave by. Optional, like the account builder below: absent ⇒ the name stays plain text, which
	 *  is what a mount with no client route should show rather than a dead link. Takes the case's
	 *  `subject_uid`, never the serial in the `Contact #123` fallback. */
	client?: (uid: string) => string;
	/** An account case's record, from the Accounts column — the case's `account_ref`. */
	account?: (uid: string) => string;
}

/** The mutations the journey detail can host. The app owns its form-action names; the component
 *  only posts to them. Absent ⇒ read-only: no step actions, no stop-journey, no task completion. */
export type JourneyDetailActions = {
	/** SvelteKit's `enhance`, injected because pkgs/ui cannot import `$app/forms`. */
	enhance: Enhance;
	/** Staff action a manual step (review outcome, task, account open), e.g. `?/action`. Identity
	 *  and AML are never decided this way: the API refuses a typed outcome on either. */
	action: string;
	/** Record an in-person terms acceptance, e.g. `?/acknowledge`. Multipart — it carries the
	 *  SIGNED COPY (`file`, a PDF scan or photograph), which the API requires. */
	acknowledge: string;
	/** Re-run a failed step kick-off, e.g. `?/retryStep`. */
	retryStep: string;
	/** Approve the setup plan a step is waiting on, e.g. `?/approveRun`. Posts the
	 *  run uid. Absent on a mount that can act on steps but does not decide actions. */
	approveRun?: string;
	/** File one manual identity document on the client's behalf, e.g. `?/identityDocument`. */
	identityDocument?: string;
	/** Take an open digital identity check onto those documents, e.g. `?/identityManual`. */
	identityManual?: string;
	/** Stop the journey, e.g. `?/cancel`. */
	cancel: string;
	/** Settle a task raised against the journey, e.g. `?/completeTask`. */
	completeTask: string;
	/** Set who owns the journey's platform-side tasks, e.g. `?/assign`. Optional: a mount that
	 *  can act on steps but has no staff directory still shows the current name as text. */
	assign?: string;
	/** Grant this profile's client a portal login (create-or-reuse + link + invite email) in one
	 *  step, e.g. `?/invitePortal`. Optional and separately gated: it needs `contacts:write` AND
	 *  `members:write` on the API side, a stricter bar than the `cases:write` the rest of this
	 *  object runs on, so a `cases:write`-only caller gets every other action with this one
	 *  absent rather than a button that renders and then 403s. */
	invitePortal?: string;
};

/** One platform member the journey can be assigned to. Not filtered to advisers. */
export interface JourneyStaff {
	user_id: string;
	name: string | null;
	email: string | null;
}
