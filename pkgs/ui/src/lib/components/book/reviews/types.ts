/**
 * Data shapes the shared review surfaces read (structural — an app's richer API types are
 * assignable). The row vocabulary itself lives in ./reviews.ts.
 */

export type ReviewStatus = 'pending' | 'approved' | 'rejected' | 'cancelled' | 'conflicted';

export type ReviewScope = 'inbox' | 'mine' | 'all';

/**
 * The SEAT a reader takes on the queue: what is theirs to decide, what they submitted and are
 * waiting on, everything still waiting, or what has been decided. The API's scopes (inbox · mine
 * · all) are the first three over the wire; `settled` is the fourth, read off the status.
 */
export type ReviewSeat = 'for_me' | 'mine' | 'waiting' | 'settled';

/** One held command, as GET /reviews and GET /reviews/{uid} return it. */
export type ReviewRow = {
	uid: string;
	/** Per-firm sequential case number, spoken as RV-{n}. The uid stays the route handle. */
	case_no: number;
	action_key: string;
	label: string;
	summary: string | null;
	before_label: string | null;
	after_label: string | null;
	event_type: string;
	subject_type: string;
	subject_id: string;
	contact_uid: string | null;
	contact_name: string | null;
	account_uid: string | null;
	account_name: string | null;
	justification: string;
	reviewer_role_id: number | null;
	reviewer_role_slug: string | null;
	reviewer_role_name: string | null;
	proposed_by: string;
	proposed_by_name: string | null;
	proposed_at: string;
	status: ReviewStatus;
	decided_by: string | null;
	decided_by_name: string | null;
	decided_at: string | null;
	decision_note: string | null;
	applied_at: string | null;
	is_mine: boolean;
	can_decide: boolean;
	/** Every role that may decide this case, by name — `reviewer_role_name` is only the first. */
	approver_roles?: string[];
	/**
	 * How many people, other than the one who submitted it, hold one of those roles. Zero means
	 * nobody can decide it (the submitter is the only holder); null means the gate names no role.
	 */
	eligible_approvers?: number | null;
	/** A gated child of a composite (a firm that gates account opening holds onboarding's
	 *  `account.create`), listed as its own case. */
	is_child?: boolean;
	/**
	 * `action` is a held action run shown in this inbox. Approve and reject post to that run.
	 * Set on a provider-action hold (a payment, a transfer, an account opening). A book action
	 * omits it.
	 */
	source?: 'review' | 'action';
	/** Field-level change. The listing expand and the pending card both read it. */
	payload?: Record<string, unknown> | null;
	before?: Record<string, unknown> | null;
	/**
	 * What the door recorded when it opened the run — the form as the adviser filled it (an
	 * instruction's kind and amount, every line of a transfer). A provider action has no
	 * now-vs-proposed; this is its body. A book review omits it.
	 */
	input?: Record<string, unknown> | null;
};

/** Cash movements whose form asks for a reason: the money actions that send them. A regular
 *  schedule is not one of these. */
const MONEY_REASON_ACTIONS = [
	'transaction.payment',
	'transaction.withdrawal',
	'transaction.fee'
] as const;

export function moneyNeedsReason(keys: readonly string[]): boolean {
	return keys.some((key) => (MONEY_REASON_ACTIONS as readonly string[]).includes(key));
}

/** Action keys whose form asks for a reason. Approval on its own does not. */
export function justificationKeys(policies: readonly ReviewPolicy[]): string[] {
	return policies
		.filter((policy) => policy.mode === 'four_eye' && Boolean(policy.require_justification))
		.map((policy) => policy.action_key);
}

/**
 * Book keys from the review catalogue, plus send actions that require a reason.
 * A payment, withdrawal or fee is not a book policy. Those keys come from the money-actions
 * read, which the person who can send is allowed to make.
 */
export function reasonKeys(
	policies: readonly ReviewPolicy[],
	sendKeys: readonly string[] = []
): string[] {
	const keys = new Set(justificationKeys(policies));
	for (const key of sendKeys) {
		if (key) keys.add(key);
	}
	return [...keys];
}

export type ReviewPolicy = {
	action_key: string;
	label: string;
	description: string;
	family: string;
	event_type: string;
	mode: 'off' | 'four_eye';
	/** The form asks for a reason. Approval can be on with this off. */
	require_justification?: boolean;
	reviewer_role_id: number | null;
	reviewer_role_slug: string | null;
	reviewer_role_name: string | null;
};

/** Every href the inbox renders — injected, so a read-only mount can point at its own routes. */
export type ReviewsListingLinks = {
	client?: (uid: string) => string;
	account?: (uid: string) => string;
};

export type ReviewDetailLinks = {
	reviews: () => string;
	client?: (uid: string) => string;
	account?: (uid: string) => string;
};
