// Provider operations registry — the cross-platform abstraction. Each op is
// scoped to a provider and only surfaces (avail) for transfers whose route/basis/
// status make it applicable. run() mutates the transfer (advances the saga,
// appends an event) and returns a toast message. Ported + generalised from
// docs/transfers-workbench.html.
import { LIVE, RECON_LEG_LABEL, ROUTES } from './status';
import type { ReconLeg, Transfer } from './types';

let seq = 0;
const nextSeq = () => ++seq;

function ev(t: Transfer, code: string, det: string, kind: TransferEventKind = '') {
	t.events.push({ code, det, ts: 'just now', kind });
}
type TransferEventKind = '' | 'acc' | 'bad' | 'good';

export interface Op {
	id: string;
	provider: 'Morgan Lloyd' | 'SEI' | 'Seccl' | 'Graphene';
	label: string;
	api: string;
	avail: (t: Transfer) => boolean;
	run: (t: Transfer) => string;
}

const isSei = (t: Transfer) => t.route === 'ml_sei' || t.route === 'intra_sei';

export const OPS: Op[] = [
	// ---------- Morgan Lloyd (ml_sei only) ----------
	{
		id: 'ml_create',
		provider: 'Morgan Lloyd',
		label: 'Create transfer at Morgan Lloyd',
		api: 'POST cam_createtransfer',
		avail: (t) => t.route === 'ml_sei' && t.status === 'entered',
		run: (t) => {
			t.status = 'submitted_to_ml';
			if (t.ml) t.ml.ref = t.ml.ref || 'cam-' + t.id.slice(3) + '00';
			ev(t, 'ml.submitted', 'cam_createtransfer → submitted', 'acc');
			return 'Submitted to Morgan Lloyd';
		}
	},
	{
		id: 'ml_poll',
		provider: 'Morgan Lloyd',
		label: 'Poll transfer status',
		api: 'POST cam_gettransfer',
		avail: (t) => t.route === 'ml_sei' && LIVE.has(t.status),
		run: (t) => {
			if (t.status === 'submitted_to_ml') t.status = 'in_flight';
			else if (t.status === 'in_flight') t.status = 'ml_complete';
			else if (t.status === 'ml_complete') {
				t.status = 'settlement_date_advised';
				if (t.ml) t.ml.settle = t.ml.settle || 'advised';
			} else {
				ev(t, 'ml.poll', 'cam_gettransfer → ' + t.status + ', no change', '');
				return 'Checked Morgan Lloyd. No change.';
			}
			ev(t, 'ml.poll', 'cam_gettransfer → ' + t.status, 'acc');
			return 'Morgan Lloyd status changed to ' + t.status;
		}
	},
	{
		id: 'ml_amend',
		provider: 'Morgan Lloyd',
		label: 'Amend transfer at Morgan Lloyd',
		api: 'PATCH cam_transfers/{id}',
		avail: (t) =>
			t.route === 'ml_sei' && (t.status === 'submitted_to_ml' || t.status === 'in_flight'),
		run: (t) => {
			ev(t, 'ml.transfer_updated', 'Morgan Lloyd transfer amended', 'acc');
			return 'Amended at Morgan Lloyd';
		}
	},

	// ---------- SEI as initiator (intra_sei) ----------
	{
		id: 'sei_initiate',
		provider: 'SEI',
		label: 'Initiate free movement',
		api: 'POST initiateAccountTransfer',
		avail: (t) => t.route === 'intra_sei' && t.status === 'entered',
		run: (t) => {
			t.status = 'submitted_to_ml';
			t.custody.ref = t.custody.ref || 'SEI-FM-' + (44000 + nextSeq());
			t.custodyStatus = 'IN PROCESS: SUBMITTED TO COUNTERPARTY';
			ev(t, 'sei.initiated', 'initiateAccountTransfer → submitted', 'acc');
			return 'Submitted to SEI';
		}
	},
	{
		id: 'sei_poll',
		provider: 'SEI',
		label: 'Poll pending transfer',
		api: 'POST getPendingTransfersStatus',
		avail: (t) =>
			t.route === 'intra_sei' && (t.status === 'submitted_to_ml' || t.status === 'in_flight'),
		run: (t) => {
			if (t.status === 'submitted_to_ml') {
				t.status = 'in_flight';
				t.custodyStatus = 'IN PROCESS: RECEIVED AT COUNTERPARTY';
			} else {
				t.status = 'sei_acr_created';
				t.custodyStatus = 'SETTLED';
			}
			ev(t, 'sei.pending_status', 'getPendingTransfersStatus → ' + t.custodyStatus, 'acc');
			return 'SEI status: ' + t.custodyStatus;
		}
	},

	// ---------- SEI custody (ml_sei + intra_sei) ----------
	{
		id: 'sei_acr_create',
		provider: 'SEI',
		label: 'Create cash receipt (ACR)',
		api: 'POST createCurrencyReceipt',
		avail: (t) =>
			t.route === 'ml_sei' && t.status === 'settlement_date_advised' && t.basis === 'cash',
		run: (t) => {
			t.status = 'sei_acr_created';
			t.custody.ref = t.custody.ref || 'ACR-2026-00' + (720 + nextSeq());
			t.custodyStatus = 'IN PROCESS: SUBMITTED TO COUNTERPARTY';
			ev(t, 'sei.acr_created', 'createCurrencyReceipt → ' + t.custody.ref, 'acc');
			return 'Cash receipt created at SEI';
		}
	},
	{
		id: 'sei_ftin',
		provider: 'SEI',
		label: 'Free transfer in (register assets)',
		api: 'POST initiateAccountTransferIn',
		avail: (t) =>
			t.route === 'ml_sei' && t.status === 'settlement_date_advised' && t.basis === 'in_specie',
		run: (t) => {
			t.status = 'sei_acr_created';
			t.custodyStatus = 'IN PROCESS: RECEIVED AT COUNTERPARTY';
			const n = t.assets ? t.assets.length : 0;
			ev(
				t,
				'sei.free_transfer_in',
				'initiateAccountTransferIn → ' + n + ' asset lines registered',
				'acc'
			);
			return 'Assets registered at SEI';
		}
	},
	{
		id: 'sei_settle',
		provider: 'SEI',
		label: 'Settle free movement',
		api: 'POST settlePendedItem',
		avail: (t) => isSei(t) && t.basis === 'in_specie' && t.status === 'sei_acr_created',
		run: (t) => {
			t.custodyStatus = 'SETTLED';
			ev(
				t,
				'sei.settle_free_movement',
				'settlePendedItem → freeMovementType 4 (Free Receipt) settled',
				'acc'
			);
			return 'Free movement settled';
		}
	},
	{
		id: 'sei_edit_custody',
		provider: 'SEI',
		label: 'Edit custody transfer dates',
		api: 'PATCH editCustodyTransfer',
		avail: (t) => isSei(t) && t.basis === 'in_specie' && LIVE.has(t.status),
		run: (t) => {
			ev(t, 'sei.edit_custody', 'Requested and settlement dates amended', 'acc');
			return 'Custody transfer dates edited';
		}
	},
	{
		id: 'sei_pending',
		provider: 'SEI',
		label: 'Check pending transfers',
		api: 'POST getPendingTransfersStatus',
		avail: (t) => t.route === 'ml_sei' && LIVE.has(t.status),
		run: (t) => {
			ev(
				t,
				'sei.pending_status',
				'getPendingTransfersStatus → ' + (t.custodyStatus || 'in process'),
				''
			);
			return 'Checked SEI pending transfers';
		}
	},

	// ---------- Seccl (intra_seccl) ----------
	{
		id: 'seccl_place',
		provider: 'Seccl',
		label: 'Place product transfer',
		api: 'POST /portfoliotransactiongroup',
		avail: (t) => t.route === 'intra_seccl' && t.status === 'entered',
		run: (t) => {
			t.status = 'submitted_to_ml';
			t.custody.ref = t.custody.ref || 'SECCL-PT-' + (90000 + nextSeq());
			t.cedingProgress = 'With previous provider';
			ev(t, 'seccl.placed', 'Product Transfer placed → With previous provider', 'acc');
			return 'Placed with previous provider';
		}
	},
	{
		id: 'seccl_onboard',
		provider: 'Seccl',
		label: 'Onboard assets (CREST re-reg)',
		api: 'PATCH /portfoliotransaction/{id}',
		avail: (t) => t.route === 'intra_seccl' && t.status === 'submitted_to_ml',
		run: (t) => {
			t.status = 'in_flight';
			t.cedingProgress = 'Onboarding assets';
			ev(t, 'seccl.onboarding', 'transferStatus → Onboarding assets (CREST)', 'acc');
			return 'Onboarding assets (CREST)';
		}
	},
	{
		id: 'seccl_complete',
		provider: 'Seccl',
		label: 'Mark transfer completed',
		api: 'GET /portfoliotransaction/{id}',
		avail: (t) => t.route === 'intra_seccl' && t.status === 'in_flight',
		run: (t) => {
			t.status = 'ml_complete';
			t.cedingProgress = 'Completed';
			ev(t, 'seccl.completed', 'transferStatus → Completed (cash & stock settled)', 'acc');
			return 'Transfer completed at Seccl';
		}
	},
	{
		id: 'seccl_bookcost',
		provider: 'Seccl',
		label: 'Set book cost on missing lines',
		api: 'PUT /portfoliotransaction/action/bookvalue',
		avail: (t) => !!(t.assets && t.assets.some((a) => a.book == null && !a.autoBook)),
		run: (t) => {
			let n = 0;
			t.assets?.forEach((a) => {
				if (a.book == null && !a.autoBook) {
					a.book = Math.round(a.value * 0.92 * 100) / 100;
					n++;
				}
			});
			t.actionRequired = false;
			ev(t, 'seccl.book_cost_set', 'PUT bookvalue → book cost set on ' + n + ' line(s)', 'acc');
			return 'Book cost set on ' + n + ' line(s)';
		}
	},

	// ---------- Graphene (all routes) ----------
	{
		id: 'reconcile',
		provider: 'Graphene',
		label: 'Run reconciliation',
		api: 'transfer_saga.reconcile',
		avail: (t) => t.status === ROUTES[t.route].reconFrom && !t.recon,
		run: (t) => {
			const route = ROUTES[t.route];
			const legs: ReconLeg[] = route.reconLegs.map((key) => ({
				key,
				label: RECON_LEG_LABEL[key],
				value: t.value // clean match for freshly-driven transfers
			}));
			// `?? t.value` gives a zero delta for a route with no legs, which is the honest
			// reading: there is nothing to disagree with. (noUncheckedIndexedAccess.)
			const custodianLeg = legs.at(-1);
			const delta = Math.abs((custodianLeg?.value ?? t.value) - t.value);
			t.recon = { expected: t.value, legs };
			if (delta <= 0.01) {
				t.status = 'reconciled';
				ev(t, 'transfer.reconciled', 'Custody credit matched advised within ±£0.01', 'good');
				return 'Reconciled within tolerance';
			}
			t.status = 'manual_intervention';
			t.breakType = 'settlement_value_mismatch';
			t.breakStatus = 'open';
			ev(
				t,
				'transfer.reconciliation_break',
				'Custody credit outside tolerance. Review required.',
				'bad'
			);
			return 'Reconciliation break raised';
		}
	},
	{
		id: 'notify',
		provider: 'Graphene',
		label: 'Notify adviser & close',
		api: 'POST /transfers/{uid}/notify',
		avail: (t) => t.status === 'reconciled',
		run: (t) => {
			t.status = 'adviser_notified';
			ev(t, 'transfer.adviser_notified', 'Adviser notified. Transfer closed.', 'good');
			return 'Adviser notified. Transfer closed.';
		}
	}
];

export const OP_BY_ID: Record<string, Op> = Object.fromEntries(OPS.map((o) => [o.id, o]));

/** Suggested next op for a transfer, by route + status (+ basis). */
export function nextOpId(t: Transfer): string | null {
	switch (t.status) {
		case 'entered':
			return t.route === 'ml_sei'
				? 'ml_create'
				: t.route === 'intra_sei'
					? 'sei_initiate'
					: 'seccl_place';
		case 'submitted_to_ml':
			return t.route === 'ml_sei'
				? 'ml_poll'
				: t.route === 'intra_sei'
					? 'sei_poll'
					: 'seccl_onboard';
		case 'in_flight':
			return t.route === 'ml_sei'
				? 'ml_poll'
				: t.route === 'intra_sei'
					? 'sei_poll'
					: 'seccl_complete';
		case 'ml_complete':
			return t.route === 'ml_sei' ? 'ml_poll' : 'reconcile';
		case 'settlement_date_advised':
			return t.basis === 'in_specie' ? 'sei_ftin' : 'sei_acr_create';
		case 'sei_acr_created':
			return t.basis === 'in_specie' && t.custodyStatus !== 'SETTLED' ? 'sei_settle' : 'reconcile';
		case 'reconciled':
			return 'notify';
		default:
			return null;
	}
}
