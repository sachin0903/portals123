/**
 * The self-serve money journeys — "Make a payment" and "Take money out" — for a client who may
 * move their own money (an execution-only account, or a book with `capabilities.money_movement`;
 * `$lib/account-facts`). The API's `/crm/money` door (`apps/api/app/client_money.py`) does the
 * deciding: this module is the wire shapes it answers with and the parsing of what the form posts.
 *
 * Nothing here decides thresholds or availability. The review step shows what the API's preview
 * says and the raise is re-decided server-side from the account's current position — the screen
 * is never trusted to say what the account can afford.
 */

export type MoneyKind = 'deposit' | 'withdrawal';

/** The route segment ↔ the API's word. `deposit` stays in the URL (links exist); "payment" is
 *  what the client reads. */
export const KIND_BY_ROUTE: Record<string, MoneyKind> = {
	deposit: 'deposit',
	withdraw: 'withdrawal'
};

export type MoneyFlag = { code: 'absolute' | 'account_share' | 'portfolio_share'; label: string };

export type MoneyPreview = {
	kind: MoneyKind;
	amount: number;
	instruction_kind: 'expected_in' | 'withdrawal' | 'model_withdrawal' | null;
	refusal: string | null;
	review_required: boolean;
	flags: MoneyFlag[];
	account: {
		uid: string;
		name: string;
		product_type: string | null;
		value: number | null;
		has_model: boolean;
		service_type: string | null;
	};
	portfolio_value: number | null;
	value_after: number | null;
	share_of_account: number | null;
	share_of_portfolio: number | null;
	cash_available: number | null;
	invested_available: number | null;
	from_cash: number;
	from_investments: number;
	allowance_remaining: number | null;
	allowance_after: number | null;
	/** "Barclays •••• 2241" — the nominated account a withdrawal is paid to; null when none is on file. */
	bank: string | null;
	/** ISO date the money should be in the bank by (withdrawal). */
	lands_by: string | null;
	invest_within_working_days: number | null;
	/** ISO date the payment is expected by (deposit). */
	expected_by: string | null;
	/** Where to send a payment. Null until firms can set their own (API module note). */
	pay_in: Record<string, string> | null;
};

export type MoneyRaised = {
	instruction: {
		uid: string;
		kind: string;
		amount: number;
		status: string;
		due_date: string | null;
		created_at: string | null;
	};
	review_required: boolean;
	/** The action's own approval is holding the send. Distinct from the safety-check task. */
	held: boolean;
	flags: MoneyFlag[];
	preview: MoneyPreview;
	pay_in: Record<string, string> | null;
};

export type PendingRequest = {
	uid: string;
	kind: 'expected_in' | 'withdrawal' | 'model_withdrawal';
	amount: number;
	status: string;
	due_date: string | null;
	created_at: string | null;
	account_uid: string;
	account_name: string;
	review_required: boolean;
	raised_by_client: boolean;
};

/** The step the page is on, decided by the last action's result (no client state to lose). */
export type MoneyStep = 'amount' | 'review' | 'done';

/**
 * An amount as the money field posts it: a plain number in pounds. Grouping and a currency symbol
 * are tolerated ("£1,500.00") because a browser without JS posts the field's raw text. Null for
 * anything that is not a positive, finite amount with at most two decimal places — the API
 * refuses those too, but a form should say so without a round-trip.
 */
export function parseAmount(raw: unknown): number | null {
	if (typeof raw !== 'string') return null;
	const cleaned = raw.replace(/[£\s]/g, '');
	// A plain figure, or one grouped in thousands. Stripping every comma would turn "5,0,0"
	// into 500 and post an amount nobody typed.
	if (!/^(\d+|\d{1,3}(,\d{3})+)(\.\d{1,2})?$/.test(cleaned)) return null;
	const n = Number(cleaned.replace(/,/g, ''));
	return Number.isFinite(n) && n > 0 ? n : null;
}

/** What a pending request is called on the client's own list. */
export function pendingLabel(kind: PendingRequest['kind']): string {
	return kind === 'expected_in' ? 'Payment in' : 'Withdrawal';
}
