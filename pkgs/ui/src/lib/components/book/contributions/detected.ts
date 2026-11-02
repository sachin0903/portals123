/** A credit the custodian reported, presented as the contribution it is not yet.
 *
 * `platform.contributions.status` has had `identified` since 0051 and **nothing ever produced
 * it** — every contribution was born at `awaiting_confirmation` (booked) or `confirmed`
 * (classified from a credit). This is what that stage was for: money the feed has detected,
 * before anyone has said what it is.
 *
 * Adapting a credit into a `Contribution` rather than giving it its own list is what lets the
 * workbench be one queue. An adviser working a morning's payments should not have to know
 * whether the row they are looking at came from a booking or from the feed; they should see
 * everything that needs a decision, in one place, in date order.
 *
 * The uid is prefixed `credit:` so the panel and the page can tell the two apart — a detected
 * row has no `platform.contributions` row behind it, so "save" means
 * `POST /contributions/from-transaction`, not `PATCH /contributions/{uid}`.
 */
import { matches, search } from '../listing/filters.js';
import type { ListingQuery } from '../listing/query.js';
import { stageKeyOf } from './status.js';
import type { Contribution, UnclassifiedCredit } from './types.js';

/** The uid prefix a detected row carries. */
export const DETECTED_PREFIX = 'credit:';

export const isDetected = (uid: string): boolean => uid.startsWith(DETECTED_PREFIX);

/** The transaction id behind a detected row's uid, or null if it is a real contribution. */
export function transactionIdOf(uid: string): number | null {
	if (!isDetected(uid)) return null;
	const id = Number(uid.slice(DETECTED_PREFIX.length));
	return Number.isFinite(id) ? id : null;
}

/**
 * One credit, in `Contribution` shape at status `identified`.
 *
 * Money fields that are genuinely unknown stay null rather than being computed: the relief on a
 * credit nobody has categorised is not zero, it is undetermined, and rendering £0.00 in the
 * gross-up would state a figure we have not worked out. The panel recomputes a live preview
 * from the pending category anyway, which is the right place for a provisional number.
 */
export function creditAsContribution(credit: UnclassifiedCredit): Contribution {
	return {
		id: credit.id,
		uid: `${DETECTED_PREFIX}${credit.id}`,
		account_id: credit.account_id,
		contact_id: credit.contact_id,
		expectation_id: null,
		transaction_id: credit.id,

		// The custodian's suggestion, or the commonest case. Never submitted without the adviser
		// confirming it — see credit-queue.svelte.
		category: credit.suggested_category ?? 'personal_contribution',
		amount: credit.amount,
		gross_amount: null,
		relief_amount: null,
		prefunded_ras_amount: null,
		currency: credit.currency,
		tax_year: null,
		frequency: 'single',
		paid_by: 'client',
		contribution_date: credit.transaction_date,

		status: 'identified',
		ml_contribution_id: null,
		ml_recorded_at: null,
		ml_status: null,
		ml_status_at: null,
		cancelled_at: null,
		cancelled_reason: null,
		note: null,
		created_at: credit.transaction_date ?? '',
		updated_at: credit.transaction_date ?? '',

		account_name: credit.account_name,
		account_uid: credit.account_uid,
		contact_uid: credit.contact_uid,
		product_type: credit.product_type,
		client_name: credit.client_name,
		adviser: credit.adviser,
		client_employer_name: credit.client_employer_name,

		expectation_uid: null,
		expectation_status: null,
		expectation_due_date: null,
		expectation_amount: null,
		expectation_matched_at: null,
		expectation_reference: null,
		expectation_error: null,
		matched_by_hand: null,

		transaction_amount: credit.amount,
		transaction_date: credit.transaction_date,
		transaction_currency: credit.currency,
		transaction_source: credit.source_system,
		transaction_reference: credit.source_transaction_id,
		transaction_type_label: credit.source_type,
		transaction_description_code: credit.source_subtype,
		transaction_method: credit.method,

		// The money is already here and there was never a forecast, so there is nothing to
		// compare. `matched` would claim a check nobody made.
		reconciliation: {
			status: 'pending',
			legs: [{ key: 'custodian', label: 'Received', value: credit.amount }],
			variance: null,
			reason: credit.provider_description
				? `Detected from the ${credit.source_system.toUpperCase()} feed — ${credit.provider_description}.`
				: 'Detected from the custodian feed. Classify it to create the contribution.'
		},
		relief: { status: 'not_applicable', expected: 0, received: null, outstanding: 0 },
		relief_received: null,

		employer_name: null,
		address_line1: null,
		address_line2: null,
		address_line3: null,
		city: null,
		county: null,
		postcode: null,
		phone: null,
		email: null
	};
}

/**
 * The workbench's one list: detected credits first, then everything booked.
 *
 * Detected first because they are the only rows where nothing has happened yet — a payment the
 * firm has not acknowledged is more urgent than one already moving through Morgan Lloyd. Relief
 * payments are excluded: they are shown on the credit's own row in no queue at all, because
 * classifying one as a contribution would double-count the member's annual allowance.
 */
export function workQueue(
	contributions: Contribution[],
	credits: UnclassifiedCredit[]
): Contribution[] {
	const detected = credits.filter((c) => !c.is_relief_payment).map(creditAsContribution);
	return [...detected, ...contributions];
}

/**
 * Which row a `?focus=` deep link means.
 *
 * The Action required strips on Transactions, Accounts and Clients link with the TRANSACTION's
 * uid (`{source_system}:{source_transaction_id}` — the ledger's own key), while a detected row
 * here is keyed `credit:{transaction id}`. Nothing in the queue matched, so "Categorise
 * contribution" opened the workbench with nothing selected and the adviser had to find the
 * payment again by hand. A contribution's own uid (the audit trail's links) still matches a
 * booked row directly; anything else is looked up against the credits' ledger identity.
 */
export function resolveFocus(
	focus: string | null | undefined,
	rows: Contribution[],
	credits: UnclassifiedCredit[]
): string | null {
	if (!focus) return null;
	if (rows.some((row) => row.uid === focus)) return focus;
	const credit = credits.find((c) => `${c.source_system}:${c.source_transaction_id}` === focus);
	return credit ? `${DETECTED_PREFIX}${credit.id}` : focus;
}

/**
 * The detected rows that belong ON THIS PAGE of a server-driven queue.
 *
 * The API pages and filters the BOOKED rows only; the detected credits arrive whole and the
 * queue narrows them here with the same query. Two rules, both so the pager's count and the
 * rows on screen are one answer:
 *
 * - THE QUERY APPLIES. The status, type and adviser filters and the search term narrow a
 *   detected row the same way the API narrowed a booked one — a search for one client that
 *   still showed every detected credit read as a search that ignored half the list.
 * - THE FIRST PAGE ONLY. They are the front of the queue, and all of them are shown there,
 *   unpaged; repeating them at the top of page two counted the same rows twice and left the
 *   pager's "26–50 of 100" describing thirty rows.
 */
export function detectedOnPage(rows: Contribution[], listing: ListingQuery): Contribution[] {
	if (listing.offset > 0) return [];
	const active = listing.filters ?? {};
	return rows.filter(
		(c) =>
			isDetected(c.uid) &&
			matches(active.status ?? [], stageKeyOf(c.status)) &&
			matches(active.category ?? [], c.category ?? '') &&
			matches(active.adviser ?? [], c.adviser ?? '') &&
			search(listing.q, c.uid, c.account_name, c.client_name, c.adviser, c.employer_name)
	);
}
