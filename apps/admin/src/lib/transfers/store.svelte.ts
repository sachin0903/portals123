// Reactive mock store for the cross-custodian Transfers workbench (Svelte 5 runes).
// Instantiate once per page: `const wb = new TransfersStore()`.
import { toast } from '@graphene/ui';
import { seedTransfers } from './data';
import { OP_BY_ID, OPS, nextOpId, type Op } from './ops';
import { STATUS_BUCKET, STATUSES } from './status';
import type {
	BreakStatus,
	NewTransferInput,
	Transfer,
	TransferRoute,
	TransferStatus
} from './types';

export type QueueView = 'needs' | 'all';

const PRIORITY: Record<string, number> = { break: 0, live: 1, idle: 2, done: 3 };

export class TransfersStore {
	transfers = $state<Transfer[]>(seedTransfers());

	selectedId = $state<string | null>(null);
	tab = $state('workbench');
	view = $state<QueueView>('needs');
	search = $state('');
	stageFilter = $state<TransferStatus | null>(null);
	/** Empty means unset, like the store's other list-shaped filters. */
	routeFilter = $state<TransferRoute[]>([]);

	#newSeq = 0;

	selected = $derived(this.transfers.find((t) => t.id === this.selectedId) ?? null);
	breaks = $derived(
		this.transfers.filter((t) => t.status === 'manual_intervention' && !!t.breakType)
	);
	visibleQueue = $derived(
		this.transfers
			.filter((t) => this.rowMatches(t))
			.sort(
				(a, b) =>
					PRIORITY[STATUS_BUCKET[a.status]] - PRIORITY[STATUS_BUCKET[b.status]] || b.value - a.value
			)
	);

	/** per-canonical-status board cells: count + pooled value. */
	board = $derived(
		STATUSES.map((status) => {
			const rows = this.transfers.filter((t) => t.status === status);
			return { status, count: rows.length, value: rows.reduce((s, t) => s + t.value, 0) };
		})
	);

	figures = $derived.by(() => {
		const live = this.transfers.filter((t) => STATUS_BUCKET[t.status] === 'live');
		const brk = this.breaks;
		const brkDelta = brk.reduce((s, t) => {
			if (!t.recon) return s;
			const custody = t.recon.legs.at(-1)?.value ?? t.recon.expected;
			return s + Math.abs(t.recon.expected - custody);
		}, 0);
		return {
			inFlightCount: live.length,
			inFlightValue: live.reduce((s, t) => s + t.value, 0),
			breakCount: brk.length,
			breakDelta: brkDelta,
			stalled: this.transfers.filter((t) => t.risk && STATUS_BUCKET[t.status] === 'live').length,
			settlingToday: this.transfers.filter((t) => t.settleToday).length
		};
	});

	rowMatches(t: Transfer): boolean {
		if (this.view === 'needs') {
			const needs =
				t.risk || t.status === 'manual_intervention' || t.status === 'entered' || !!t.settleToday;
			if (!needs) return false;
		}
		if (this.stageFilter && t.status !== this.stageFilter) return false;
		if (this.routeFilter.length > 0 && !this.routeFilter.includes(t.route)) return false;
		if (this.search) {
			const hay = `${t.client} ${t.provider} ${t.plan ?? ''} ${t.id} ${t.account}`.toLowerCase();
			if (!hay.includes(this.search.toLowerCase())) return false;
		}
		return true;
	}

	availOps(t: Transfer): Op[] {
		return OPS.filter((o) => o.avail(t));
	}
	nextOp(t: Transfer): Op | null {
		const id = nextOpId(t);
		return id ? (OP_BY_ID[id] ?? null) : null;
	}

	// ---- actions ----
	select(id: string | null) {
		this.selectedId = id;
	}
	toggleStage(status: TransferStatus) {
		this.stageFilter = this.stageFilter === status ? null : status;
	}
	/**
	 * Every outcome in this workbench funnels through here, which is why it is the only place that
	 * has to know how one is announced. It used to set a `toast` field that a local component
	 * rendered as a fixed dark bar at the bottom of the window — a second toast system, in a
	 * different corner from the `Toaster` the app layout already mounts.
	 */
	#notify(msg: string, api?: string) {
		toast.success(msg, api ? { description: api } : undefined);
	}

	runOp(opId: string) {
		const t = this.selected;
		const op = OP_BY_ID[opId];
		if (!t || !op || !op.avail(t)) return;
		const msg = op.run(t);
		this.#notify(msg, op.api);
	}

	forceStep(to: TransferStatus, reason: string) {
		const t = this.selected;
		if (!t) return;
		t.status = to;
		if (to !== 'manual_intervention') {
			t.risk = false;
			t.breakStatus = t.breakStatus && to === 'reconciled' ? t.breakStatus : t.breakStatus;
		}
		t.events.push({
			code: 'transfer.force_step',
			det: `Moved to ${to}${reason ? ': ' + reason : ''}`,
			ts: 'just now',
			kind: 'acc'
		});
		this.#notify(`Forced to ${to}`, 'POST /admin/transfers/{uid}/force-step');
	}

	resolveBreak(status: BreakStatus, note: string) {
		const t = this.selected;
		if (!t) return;
		t.breakStatus = status;
		if (status === 'resolved' || status === 'accepted') {
			t.status = 'reconciled';
			t.risk = false;
			t.events.push({
				code: 'transfer.break_resolved',
				det: `Issue ${status}${note ? ': ' + note : ''}`,
				ts: 'just now',
				kind: 'good'
			});
			this.#notify('Break resolved', 'POST /admin/breaks/{id}/resolve');
		} else {
			t.events.push({
				code: 'transfer.break_updated',
				det: `Issue ${status}${note ? ': ' + note : ''}`,
				ts: 'just now',
				kind: ''
			});
			this.#notify(`Break marked ${status}`, 'POST /admin/breaks/{id}/resolve');
		}
	}

	createTransfer(input: NewTransferInput): string {
		const n = ++this.#newSeq;
		const id = 'tr_new' + String(n).padStart(3, '0');
		const custodian = input.route === 'intra_seccl' ? 'seccl' : 'sei';
		const schemeAdmin = input.route === 'ml_sei' ? 'ml' : null;
		const t: Transfer = {
			id,
			client: input.client,
			account: input.account || 'acc_new',
			firm: input.firm || 'Your firm',
			firmId: 'firm_self',
			route: input.route,
			custodian,
			schemeAdmin,
			provider: input.provider,
			plan: input.plan || null,
			wrapper: input.wrapper,
			value: input.value,
			basis: input.basis,
			scope: input.scope,
			status: 'entered',
			risk: false,
			inStage: '0d',
			created: 'today',
			ml: schemeAdmin === 'ml' ? { scheme: null, ref: null, settle: null } : null,
			custody: { acct: null, ref: null },
			recon: null,
			assets: input.basis === 'in_specie' ? (input.assets ?? []) : null,
			events: [
				{
					code: 'transfer.created',
					det: `Recorded · ${input.basis}, ${input.scope}`,
					ts: 'just now',
					kind: ''
				}
			]
		};
		this.transfers.push(t);
		this.#notify(`Recorded transfer for ${t.client}`, 'POST /transfers');
		return id;
	}
}
