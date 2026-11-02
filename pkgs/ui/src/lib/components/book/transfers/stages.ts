/** The consolidated stages the workbench, the filter and the API agree on.
 *
 * One key per status. The reconciled `completed` stage and `cancelled` are filter answers, not
 * cards. Mirrored by `app.transfers.STAGES` — the pin parses this constant, so the shape below
 * is the contract.
 */
import type { StatusTone } from '../../ui/status-badge/index.js';
import {
	SAGA,
	TRANSFER_STATUS_LABEL,
	TRANSFER_STATUS_TONE,
	type ApiTransfer,
	type ApiTransferStatus
} from './api.js';

export type StageSummary = Record<string, { amount: number; count: number }>;

export const STAGES: {
	key: string;
	label: string;
	actor: string;
	statuses: ApiTransferStatus[];
}[] = [
	{ key: 'requested', label: 'Raised', actor: 'Adviser', statuses: ['requested'] },
	{ key: 'submitted', label: 'Submitted', actor: 'Provider', statuses: ['submitted'] },
	{
		key: 'in_progress',
		label: 'At ceding scheme',
		actor: 'Ceding scheme',
		statuses: ['in_progress']
	},
	{
		key: 'assets_received',
		label: 'At administrator',
		actor: 'Scheme administrator',
		statuses: ['assets_received']
	},
	{ key: 'funds_sent', label: 'Completed', actor: 'Custodian', statuses: ['funds_sent'] },
	{
		key: 'manual_intervention',
		label: 'Needs attention',
		actor: 'Graphene',
		statuses: ['manual_intervention']
	},
	{ key: 'completed', label: 'Complete', actor: 'Graphene', statuses: ['completed'] },
	{ key: 'cancelled', label: 'Cancelled', actor: 'Graphene', statuses: ['cancelled'] }
];

/** The six the KPI strip counts. The reconciled stage and cancelled are filters, not cards. */
export const METRIC_STAGES = STAGES.filter(
	(stage) => stage.key !== 'completed' && stage.key !== 'cancelled'
);

/** The custodian filter, or nothing when the firm has fewer than two custodians switched on.
 *  One custodian is not a choice. */
export function custodianFilterOptions(
	custodians: readonly { code: string; name: string }[]
): { value: string; label: string }[] | null {
	if (custodians.length < 2) return null;
	return custodians.map((custodian) => ({ value: custodian.code, label: custodian.name }));
}

export const ROUTE_LABEL: Record<string, string> = {
	ml_sei: 'Morgan Lloyd + SEI',
	seccl: 'Seccl',
	sei: 'SEI',
	unknown: 'Unrouted'
};

/** Morgan Lloyd administers an SEI SIPP. A Seccl account, including a Seccl SIPP, is `seccl`. */
const ML_ROUTES = new Set(['ml_sei']);

/** Seccl accepts a cancellation request only in these statuses. Same set as `SECCL_CANCELLABLE`. */
const SECCL_CANCELLABLE = new Set([
	'with previous provider',
	'awaiting documentation',
	'reviewing documentation',
	'verifying documentation'
]);

const LODGED = new Set(['instructed', 'matched', 'reconciled']);

export type TransferStepId =
	| 'submit_ml'
	| 'refresh_ml'
	| 'cancel_ml'
	| 'place_seccl'
	| 'refresh_seccl'
	| 'cancel_seccl'
	| 'lodge_sei'
	| 'submit_sei'
	| 'free_receipt'
	| 'poll_sei';

export interface TransferStep {
	id: TransferStepId;
	label: string;
}

export function isDetected(transfer: ApiTransfer): boolean {
	return transfer.detected === true || transfer.uid.startsWith('detected:');
}

function fold(value: string | null | undefined): string {
	return (value ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
}

/** Seccl has taken a cancellation request and has not confirmed it.
 *
 * That is not the transfer moving on, so it must not read as "At ceding scheme", and it is
 * not cancelled until Seccl says so. */
export function cancellationRequested(
	transfer: Pick<ApiTransfer, 'status' | 'ceding_status'>
): boolean {
	if (transfer.status === 'cancelled' || transfer.status === 'completed') return false;
	return fold(transfer.ceding_status) === 'pending cancellation';
}

/** The status an adviser reads. A pending cancellation names the request, not the saga stage. */
export function statusLabel(transfer: Pick<ApiTransfer, 'status' | 'ceding_status'>): string {
	// "Cancelling" in the badge, where the column is narrow. The rail in the panel has room
	// and still says "Cancellation requested".
	if (cancellationRequested(transfer)) return 'Cancelling';
	return TRANSFER_STATUS_LABEL[transfer.status];
}

export function statusTone(transfer: Pick<ApiTransfer, 'status' | 'ceding_status'>): StatusTone {
	if (cancellationRequested(transfer)) return 'neutral';
	return TRANSFER_STATUS_TONE[transfer.status];
}

/** The stages this route draws. At administrator is the SEI SIPP leg. Complete is
 *  never drawn: the flow ends at Completed, when the custodian has the assets. */
export function railStages(route: string | null | undefined) {
	const stages =
		route === 'ml_sei' ? SAGA : SAGA.filter((stage) => stage.status !== 'assets_received');
	return stages.filter((stage) => stage.status !== 'completed');
}

export interface ReconColumn {
	heading: 'Requested' | 'Received' | 'Transferred';
	amount: number | null;
	/** Who that figure belongs to, printed under the amount. */
	party: string;
}

/** Morgan Lloyd has the money once its own status says so, or once our saga has passed that stage. */
function schemeHasReceived(transfer: ApiTransfer): boolean {
	if (transfer.status === 'cancelled') return false;
	if (transfer.ml_status != null) return transfer.ml_status === 3 || transfer.ml_status === 4;
	return (
		transfer.status === 'assets_received' ||
		transfer.status === 'funds_sent' ||
		transfer.status === 'completed'
	);
}

/** The custodian has booked the movement. Before that, Seccl and SEI have no received figure. */
function custodianHasBooked(transfer: ApiTransfer): boolean {
	return transfer.status === 'funds_sent' || transfer.status === 'completed';
}

/** Requested against what the custodian received. Morgan Lloyd inserts the scheme administrator.

Seccl publishes one number, `transferValue`. `amount`, `executedAmount` and `consideration`
are empty on the transfers we hold, and `bookValue` is a CGT cost filled while the transfer
is still open. Received is that number once the custodian has booked it, and blank before.
A Graphene-raised transfer can differ: Received is the matched transaction, which is null
until the credit lands. */
export function reconciliationColumns(transfer: ApiTransfer): ReconColumn[] {
	const requested = transfer.total_value;
	const received = custodianHasBooked(transfer) ? transfer.transaction_amount : null;
	if (!ML_ROUTES.has(transfer.route ?? '')) {
		return [
			{ heading: 'Requested', amount: requested, party: 'Ceding provider' },
			{ heading: 'Received', amount: received, party: 'Custodian' }
		];
	}
	return [
		{ heading: 'Requested', amount: requested, party: 'Ceding provider' },
		{
			heading: 'Received',
			amount: schemeHasReceived(transfer) ? requested : null,
			party: 'Scheme admin'
		},
		{ heading: 'Transferred', amount: received, party: 'Custodian' }
	];
}

/** The one next instruction. Null for a detected, completed or cancelled row. */
export function nextStep(transfer: ApiTransfer): TransferStep | null {
	if (isDetected(transfer)) return null;
	if (transfer.status === 'completed' || transfer.status === 'cancelled') return null;
	const route = transfer.route ?? 'unknown';

	if (ML_ROUTES.has(route)) {
		if (transfer.status === 'requested') {
			return { id: 'submit_ml', label: 'Submit to Morgan Lloyd' };
		}
		// The custodian receipt waits until Morgan Lloyd has completed the transfer (funds sent).
		// Generating it while Morgan Lloyd is still holding the cash asks SEI to expect money
		// that has not left the scheme administrator.
		if (route === 'ml_sei' && transfer.status === 'funds_sent') {
			if (transfer.basis === 'in_specie' && !transfer.sei_activity_id && !transfer.sei_accepted) {
				return { id: 'free_receipt', label: 'Register free receipt' };
			}
			if (
				transfer.basis !== 'in_specie' &&
				!LODGED.has(transfer.expectation_status ?? '') &&
				!transfer.sei_activity_id &&
				!transfer.sei_accepted
			) {
				return { id: 'lodge_sei', label: 'Generate ACR with custodian' };
			}
			// A receipt activity id is not a pending transferIn. Polling it 422s.
			// Morgan Lloyd still owns the saga, so the footer stays on that refresh.
		}
		if (
			transfer.status === 'submitted' ||
			transfer.status === 'in_progress' ||
			transfer.status === 'assets_received' ||
			transfer.status === 'funds_sent' ||
			transfer.status === 'manual_intervention'
		) {
			return { id: 'refresh_ml', label: 'Refresh from Morgan Lloyd' };
		}
		return null;
	}

	if (route === 'seccl') {
		if (transfer.status === 'requested' && !transfer.seccl_transfer_id && !transfer.seccl_placed) {
			return { id: 'place_seccl', label: 'Place at Seccl' };
		}
		// Refresh addresses the id Seccl gave us. A place that came back without one has
		// nothing to read, and offering it would 422.
		if (transfer.seccl_transfer_id) {
			return { id: 'refresh_seccl', label: 'Refresh from Seccl' };
		}
		return null;
	}

	if (route === 'sei') {
		// An activity id means SEI already has the instruction, so submit is not offered again.
		// `sei_accepted` is the same gate when the body had no id to store. Pending-transfer
		// status only lists transferIn, so only that basis is polled.
		if (
			!transfer.sei_activity_id &&
			!transfer.sei_accepted &&
			(transfer.status === 'requested' || transfer.status === 'manual_intervention')
		) {
			return transfer.basis === 'in_specie'
				? { id: 'submit_sei', label: 'Submit transfer in' }
				: { id: 'lodge_sei', label: 'Lodge anticipated receipt' };
		}
		// Pending-transfer status only lists transferIn. A cash-receipt id is not in
		// that list, so Refresh from SEI would 422 after a lodge that succeeded.
		if (transfer.basis === 'in_specie' && transfer.sei_activity_id) {
			return { id: 'poll_sei', label: 'Refresh from SEI' };
		}
		return null;
	}

	return null;
}

/** Cancel, where the provider will take it. Never the primary.

A feed row has no Morgan Lloyd id and SEI has no cancel call. Seccl will cancel a product
transfer that is still with the previous provider — that is Submitted — or in its
documentation checks, including one that arrived on the feed. A stock migration has no
transfer status, so it is not offered. */
export function cancelStep(transfer: ApiTransfer): TransferStep | null {
	if (transfer.status === 'completed' || transfer.status === 'cancelled') return null;
	const route = transfer.route ?? 'unknown';
	if (
		!isDetected(transfer) &&
		ML_ROUTES.has(route) &&
		(transfer.status === 'submitted' || transfer.status === 'in_progress')
	) {
		return { id: 'cancel_ml', label: 'Cancel at Morgan Lloyd' };
	}
	if (route === 'seccl' && SECCL_CANCELLABLE.has(fold(transfer.ceding_status))) {
		return { id: 'cancel_seccl', label: 'Request cancellation' };
	}
	return null;
}
