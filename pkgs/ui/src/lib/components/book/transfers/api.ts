/** Transfers as the API returns them (`GET /transfers`) — the real saga, not the prototype's.
 *
 * ## Why this file exists beside `types.ts`
 *
 * `types.ts` is the workbench PROTOTYPE's vocabulary: nine statuses, three custody routes, a
 * per-route stage rail. Its own header says it mirrors
 * `apps/api/migrations/sql/0003_pensions_ledger.sql` — a migration that was superseded by
 * `0051_contributions_ledger.sql` and never shipped. So those nine statuses describe a schema
 * that does not exist, and the rail was rendering invented states.
 *
 * What the providers actually publish was checked before this was written:
 *
 * - **Morgan Lloyd** documents a six-value progress set (`morgan_lloyd.enums.TransferStatus`):
 *   Awaiting forms · ML to request transfer · Awaiting action from ceding scheme · Funds
 *   received · Funds sent · No longer transferring. That is the real ceding-side vocabulary.
 * - **SEI** has no transfer entity at all. The SEI leg is an Anticipated Cash Receipt and then a
 *   realised credit — which is why a transfer's progress ends at our own reconciliation rather
 *   than at anything SEI says.
 * - **Seccl** documents a `transferStatus` enum: With Previous Provider · Onboarding Assets ·
 *   Completed · Rejected · Deleted (plus the documentation and sell-down statuses). It is
 *   stored verbatim as `ceding_status` and mapped by `app.transfers.seccl_stage`. Seccl
 *   `Completed` is `funds_sent` (shown as Completed), not our reconciled `completed`.
 * - **SEI** pending-transfer codes map the same way when SEI is the administrator. On a
 *   Morgan Lloyd transfer an SEI poll updates the custodian line only.
 *
 * The prototype types stay (the mock store and its components still compile against them, and
 * the fixtures are useful for previews); the real surface uses these.
 */

import type { StatusTone } from '../../ui/status-badge/index.js';
import type { Reconciliation } from '../contributions/types.js';

export type { Reconciliation };

/** `platform.transfers.status` — Graphene's own saga, widened by `0056` with `funds_sent`.
 *
 * `funds_sent` exists because Morgan Lloyd distinguishes it: "Funds received" (the ceding
 * scheme has paid ML) and "Funds sent" (ML has passed it to the custodian) are different weeks
 * of chasing, and collapsing them made a transfer look stalled while the money was moving. */
export type ApiTransferStatus =
	| 'requested'
	| 'submitted'
	| 'in_progress'
	| 'assets_received'
	| 'funds_sent'
	| 'completed'
	| 'cancelled'
	| 'manual_intervention';

export type ApiTransferType = 'pension' | 'isa' | 'gia' | 'cash' | 'in_specie';

/** Morgan Lloyd's `transferstatus` / `cam_transferstatus`, mirrored verbatim. */
export const ML_TRANSFER_STATUS = {
	awaitingForms: 0,
	toRequest: 1,
	awaitingCedingScheme: 2,
	fundsReceived: 3,
	fundsSent: 4,
	noLongerTransferring: 5
} as const;

export const ML_TRANSFER_STATUS_LABELS: Record<number, string> = {
	0: 'Awaiting forms',
	1: 'ML to request transfer',
	2: 'Awaiting action from ceding scheme',
	3: 'Funds received',
	4: 'Funds sent',
	5: 'No longer transferring'
};

export interface ApiTransfer {
	id: number;
	/** `trf_…` for a booked transfer, `detected:<transaction id>` for one read off the feed. */
	uid: string;
	/** True when this came from the custodian's transaction feed rather than from a transfer
	 *  someone raised here. Read-only: nothing to place or chase.
	 *
	 *  These exist because `platform.transfers` only holds what we booked, so the workbench was
	 *  empty while Seccl was reporting real transfer traffic on the transaction feed
	 *  (`is_pension_transfer`, `transfer_status`, `previous_provider_name`). */
	detected?: boolean;
	account_id: number;
	contact_id: number | null;
	expectation_id: number | null;
	transaction_id: number | null;

	ceding_provider: string;
	policy_number: string | null;
	transfer_type: ApiTransferType;
	/** How the money moves. Distinct from `transfer_type` (the receiving wrapper). Null on a
	 *  row raised before the drawer asked, and on a detected feed row we could not classify. */
	basis: 'cash' | 'in_specie' | null;
	/** How much of the ceding pot moves. Null when unknown (detected rows; old booked rows). */
	extent: 'full' | 'partial' | null;
	crystallised_value: number | null;
	uncrystallised_value: number | null;
	total_value: number | null;
	currency: string;
	/** Uncrystallised (accumulation) / partial / crystallised. Null on a non-pension. */
	crystallisation: 'uncrystallised' | 'partial' | 'crystallised' | null;
	/** Morgan Lloyd `cam_pensiontype` (1–20). Null on a non-pension. */
	pension_type: number | null;
	/** Pension Schemes Act 2015 s.48(8). Null on a non-pension. */
	safeguarded_benefits: boolean | null;

	status: ApiTransferStatus;
	/** Why the last provider call could not be finished. Cleared once a later call succeeds.
	 *  Still set on a hold (`manual_intervention`) where the provider accepted the instruction
	 *  and we have no id to address a second one. */
	provider_error?: string | null;
	/** Seccl created the product transfer and the follow-up read had no id. Place is refused. */
	seccl_placed?: boolean;
	/** SEI accepted the instruction and the body had no activity id. Submit and poll are refused. */
	sei_accepted?: boolean;
	/** Morgan Lloyd's own progress, or null when the transfer has not been pushed there. */
	ml_status: number | null;
	ml_status_label: string | null;
	ml_status_at: string | null;
	/** The custodian's own words. Mapped into the saga by the provider refresh, and still shown. */
	ceding_status: string | null;
	seccl_transfer_id?: string | null;
	sei_activity_id?: string | null;
	custodian?: string | null;
	scheme_administrator?: string | null;
	/** `ml_sei` (SEI SIPP, Morgan Lloyd) | `seccl` | `sei` | `unknown`. */
	route?: string | null;
	/** Who the panel quotes, and the stage their words map to. Null when there is nothing to quote. */
	provider_party?: string | null;
	provider_native?: string | null;
	provider_stage?: string | null;
	ml_transfer_id: string | null;
	ml_recorded_at: string | null;
	expected_date: string | null;
	completed_at: string | null;
	note: string | null;
	created_at: string;
	updated_at: string;

	account_name: string | null;
	account_uid: string | null;
	product_type: string | null;
	client_name: string | null;
	/** The client's uid, which is what a link needs: `contact_id` above is a serial and the
	 *  client record resolves by uid. Null on a detected feed row, where there is no contact
	 *  join behind the name. */
	contact_uid: string | null;
	adviser: string | null;

	expectation_uid: string | null;
	expectation_status: string | null;
	expectation_due_date: string | null;
	expectation_amount: number | null;
	expectation_matched_at: string | null;
	matched_by_hand: boolean | null;

	transaction_amount: number | null;
	transaction_date: string | null;
	transaction_source: string | null;
	transaction_reference: string | null;

	/** The three-way compare, derived server-side. Literally the contributions type, not a
	 *  same-shaped copy: one `app/matching.py::reconcile` produces both, so one type describes
	 *  both and `ReconciliationPanel` renders either without a cast. */
	reconciliation: Reconciliation;

	/** Holdings on an in-specie transfer. Empty on cash, and when the custodian has not
	 *  broken the transfer down yet. */
	instruments?: TransferInstrument[];
}

/** One holding moving in specie. Seccl publishes these as stock transfers on the product
 *  transfer's link. SEI publishes the identifier and units the adviser advised, without a
 *  per-holding status. */
export interface TransferInstrument {
	name: string | null;
	code: string | null;
	quantity: number | null;
	status: string | null;
}

export interface SagaStage {
	status: ApiTransferStatus;
	label: string;
	/** Who moves it out of this stage. Half the saga is waiting on somebody else, and naming
	 *  them is what makes the rail worth reading. */
	actor: string;
}

/** The rail. One model, not one per "route": the route concept was a property of the
 *  superseded schema, and the stages below are the same whoever the custodian is — what
 *  changes is who the actor happens to be, which the transfer itself says. */
export const SAGA: SagaStage[] = [
	{ status: 'requested', label: 'Raised', actor: 'Adviser' },
	{ status: 'submitted', label: 'Submitted', actor: 'Scheme administrator' },
	{ status: 'in_progress', label: 'At ceding scheme', actor: 'Ceding scheme' },
	{ status: 'assets_received', label: 'At administrator', actor: 'Scheme administrator' },
	{ status: 'funds_sent', label: 'Completed', actor: 'Custodian' },
	// Reconciled. Kept so a transfer that has reached it still sits after Completed on the
	// index. The rail does not draw it: reconciliation is its own section, and the flow ends
	// when the custodian has the assets.
	{ status: 'completed', label: 'Complete', actor: 'Graphene' }
];

export const TRANSFER_STATUS_LABEL: Record<ApiTransferStatus, string> = {
	requested: 'Raised',
	submitted: 'Submitted',
	in_progress: 'At ceding scheme',
	assets_received: 'At administrator',
	funds_sent: 'Completed',
	completed: 'Complete',
	cancelled: 'Cancelled',
	manual_intervention: 'Needs attention'
};

/** `StatusBadge` tone by what the stage means, the scheme the contributions workbench uses. */
export const TRANSFER_STATUS_TONE: Record<ApiTransferStatus, StatusTone> = {
	requested: 'neutral',
	submitted: 'info',
	in_progress: 'info',
	assets_received: 'info',
	funds_sent: 'success',
	completed: 'success',
	cancelled: 'neutral',
	manual_intervention: 'danger'
};

/** Off-saga terminals — ways a transfer stops, not stages you pass through. */
export const OFF_SAGA: ReadonlySet<ApiTransferStatus> = new Set([
	'cancelled',
	'manual_intervention'
]);

/** How far along the rail, or -1 when off it. */
export function sagaIndex(status: ApiTransferStatus): number {
	return SAGA.findIndex((stage) => stage.status === status);
}

/** Days since the transfer was raised. Transfers are chased by age, so the number matters more
 *  than the date — a column of "07 Jun 2026" makes you do the arithmetic yourself. */
export function ageInDays(transfer: ApiTransfer, now: Date = new Date()): number | null {
	const opened = new Date(transfer.created_at).getTime();
	if (Number.isNaN(opened)) return null;
	return Math.max(0, Math.floor((now.getTime() - opened) / 86_400_000));
}

/** `3d`, `2w`, `5mo`. Sorts on the day count, not this label — otherwise "2w" collates
 *  before "3d". */
export function ageLabel(transfer: ApiTransfer, now: Date = new Date()): string {
	const days = ageInDays(transfer, now);
	if (days === null) return '—';
	if (days === 0) return 'today';
	if (days < 14) return `${days}d`;
	if (days < 60) return `${Math.round(days / 7)}w`;
	return `${Math.round(days / 30)}mo`;
}

/** The value to show: what actually landed if it has, else what is expected. Falling back
 *  matters because `total_value` is optional — a transfer can be raised before the ceding
 *  scheme has said what it is worth. */
export function transferValue(transfer: ApiTransfer): number | null {
	return transfer.transaction_amount ?? transfer.total_value ?? transfer.expectation_amount;
}

/** Cash or in-specie, from the `basis` column. Unknown is an em dash, never Cash — a pension
 *  wrapper is not a cash transfer, and treating it as one is how the workbench used to lie. */
export function basisLabel(transfer: Pick<ApiTransfer, 'basis'>): string {
	switch (transfer.basis) {
		case 'in_specie':
			return 'In-specie';
		case 'cash':
			return 'Cash';
		default:
			return '—';
	}
}

export const TRANSFER_BASIS_OPTIONS = [
	{ value: 'cash', label: 'Cash' },
	{ value: 'in_specie', label: 'In-specie' }
] as const;

export const TRANSFER_EXTENT_OPTIONS = [
	{ value: 'full', label: 'Full' },
	{ value: 'partial', label: 'Partial' }
] as const;

/** Drawdown status. The stored values stay the crystallisation words the API and Seccl's
 *  `productDrawdownStatus` map already use: no drawdown is uncrystallised, partial is some
 *  of the pot, full is the whole transfer. */
export const TRANSFER_CRYSTALLISATION_OPTIONS = [
	{ value: 'uncrystallised', label: 'No Drawdown' },
	{ value: 'partial', label: 'Partial Crystallisation' },
	{ value: 'crystallised', label: 'Full Crystallisation' }
] as const;

/** Morgan Lloyd `cam_pensiontype` (1–20). Labels are the vendor's. */
export const PENSION_TYPE_OPTIONS = [
	{ value: '1', label: 'Additional Voluntary Contribution' },
	{ value: '2', label: 'Annuity' },
	{ value: '3', label: 'APPP - Protected Rights' },
	{ value: '4', label: 'Bulk Buy-Out Plan' },
	{ value: '5', label: 'Contracted In Money Purchase' },
	{ value: '6', label: 'Contracted Out Money Purchase' },
	{ value: '7', label: 'Drawdown' },
	{ value: '8', label: 'Executive Personal Pension' },
	{ value: '9', label: 'Final Salary Scheme' },
	{ value: '10', label: 'Free Standing Additional Voluntary Contribution' },
	{ value: '11', label: 'Group Personal Pension' },
	{ value: '12', label: 'Group Stakeholder Pension' },
	{ value: '13', label: 'Other' },
	{ value: '14', label: 'Personal Pension Plan' },
	{ value: '15', label: 'QROPS' },
	{ value: '16', label: 'Retirement Annuity Contract' },
	{ value: '17', label: 'Section 32 Policy Buy Out Bond' },
	{ value: '18', label: 'Self Invested Personal Pension' },
	{ value: '19', label: 'Small Self Administered Scheme' },
	{ value: '20', label: 'Stakeholder' }
] as const;

export const SAFEGUARDED_BENEFITS_QUESTION =
	'Does the pension that is being transferred contain Safeguarded Rights (as defined in Section 48(8) of the Pension Schemes Act 2015)?';

/** Shown beside the FCA reference once safeguarded rights are confirmed. */
export const SAFEGUARDED_FCA_INFO =
	'The FCA reference number of the advice firm that provided the advice around transferring the pension with Safeguarded Rights.';

/** The address Seccl sends as `transferAccountAddress` is the client's, as the ceding scheme holds it. */
export const CEDING_CLIENT_ADDRESS_INFO =
	'This must exactly match the client address on record with the ceding provider';

export type TransferCrystallisation = (typeof TRANSFER_CRYSTALLISATION_OPTIONS)[number]['value'];

export function crystallisationLabel(value: ApiTransfer['crystallisation']): string {
	if (!value) return '—';
	return TRANSFER_CRYSTALLISATION_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

export function pensionTypeLabel(code: number | null): string {
	if (code == null) return '—';
	return (
		PENSION_TYPE_OPTIONS.find((option) => option.value === String(code))?.label ?? String(code)
	);
}

/** Crystallised and uncrystallised amounts from the status. The remainder is derived so the
 *  total cannot disagree. */
export function splitForCrystallisation(
	crystallisation: TransferCrystallisation,
	total: number,
	crystallisedAmount: number | null
): { crystallised: number; uncrystallised: number } {
	if (crystallisation === 'uncrystallised') {
		return { crystallised: 0, uncrystallised: total };
	}
	const amount = crystallisedAmount ?? 0;
	return { crystallised: amount, uncrystallised: total - amount };
}

export type TransferFormSpec = {
	askExtent: boolean;
	askCrystallisedSplit: boolean;
	askPensionFacts: boolean;
	askPensionType: boolean;
	defaultExtent: 'full' | null;
	allowCash: boolean;
	askCedingAddress: boolean;
	askSeiInstruction: boolean;
};

/** Same account fields `app.transfers.form_spec_for` reads. Mirrored here so the drawer needs
 *  no extra round trip. Morgan Lloyd applies only when the account is an SEI pension whose
 *  scheme administrator is `morgan-lloyd` — that literal matches `providers.administrators()`
 *  today, and a Python test pins the tuple. A Seccl SIPP is Seccl for both roles, so a stray
 *  Morgan Lloyd mapping does not ask the split or `cam_pensiontype`. Cash is refused on a
 *  direct SEI pension. A Morgan Lloyd SIPP can be cash: the anticipated receipt is lodged
 *  once the funds have been sent. An in-specie raise does not list holdings. */
export function formSpecFor(account: {
	custodian?: string | null;
	scheme_administrator?: string | null;
	is_pension?: boolean | null;
}): TransferFormSpec {
	const mlSipp =
		account.custodian === 'sei' &&
		Boolean(account.is_pension) &&
		account.scheme_administrator === 'morgan-lloyd';
	const seccl = account.custodian === 'seccl';
	const sei = account.custodian === 'sei';
	const seiPension = sei && Boolean(account.is_pension);
	return {
		askExtent: !seccl,
		askCrystallisedSplit: mlSipp,
		askPensionFacts: Boolean(account.is_pension),
		askPensionType: mlSipp,
		defaultExtent: seccl ? 'full' : null,
		allowCash: !seiPension || mlSipp,
		askCedingAddress: seccl,
		askSeiInstruction: sei && !mlSipp
	};
}
