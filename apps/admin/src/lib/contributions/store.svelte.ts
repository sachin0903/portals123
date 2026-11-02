// Reactive mock store for the Contributions workbench (Svelte 5 runes).
// Instantiate once per page: `const wb = new WorkbenchStore()` and pass it to subcomponents.
// Two orthogonal state machines: contribution status + match lifecycle.
import { toast } from '@graphene/ui';
import { seedContributions, seedSeiPool } from './data';
import { PIPELINE } from './status';
import type { Contribution, ContributionStatus, Match, SeiTransaction } from './types';

export type QueueFilter = 'all' | 'action' | 'personal' | 'employer';

export interface NewContributionInput {
	member: string;
	seiAccount: string;
	scheme: string;
	net: number;
	type: Contribution['type'];
	freq: Contribution['freq'];
	source: Contribution['source'];
	taxYear: string;
}

export class WorkbenchStore {
	contributions = $state<Contribution[]>(seedContributions());
	seiPool = $state<SeiTransaction[]>(seedSeiPool());
	matches = $state<Match[]>([]);

	// UI state
	selectedId = $state<number | null>(null);
	filter = $state<QueueFilter>('all');
	search = $state('');
	stageFilter = $state<ContributionStatus[] | null>(null);
	tab = $state('workbench');

	#matchSeq = 0;

	// Derived
	selected = $derived(this.contributions.find((c) => c.id === this.selectedId) ?? null);
	visibleQueue = $derived(this.contributions.filter((c) => this.rowMatches(c)));
	pendingApprovals = $derived(this.matches.filter((m) => m.status === 'pending_approval'));
	unmatchedPool = $derived(this.seiPool.filter((t) => t.matchedContributionId === null));

	rowMatches(c: Contribution): boolean {
		if (this.stageFilter && !this.stageFilter.includes(c.status)) return false;
		if (this.filter === 'action' && !['identified', 'awaiting_confirmation'].includes(c.status))
			return false;
		if (this.filter === 'personal' && c.type !== 'personal') return false;
		if (this.filter === 'employer' && c.type !== 'employer') return false;
		if (this.search) {
			const hay = `${c.member} ${c.seiAccount} ${c.scheme}`.toLowerCase();
			if (!hay.includes(this.search.toLowerCase())) return false;
		}
		return true;
	}

	countForStage(statuses: ContributionStatus[]): number {
		return this.contributions.filter((c) => statuses.includes(c.status)).length;
	}

	matchFor(contributionId: number): Match | null {
		// the active (non-rejected) match for a contribution, latest first
		const live = this.matches.filter(
			(m) => m.contributionId === contributionId && m.status !== 'rejected'
		);
		return live.at(-1) ?? null;
	}

	seiTxFor(contributionId: number): SeiTransaction | null {
		return this.seiPool.find((t) => t.matchedContributionId === contributionId) ?? null;
	}

	contributionFor(matchId: string): Contribution | null {
		const m = this.matches.find((x) => x.id === matchId);
		return m ? (this.contributions.find((c) => c.id === m.contributionId) ?? null) : null;
	}

	// ---- UI actions ----
	select(id: number | null) {
		this.selectedId = id;
	}
	toggleStage(statuses: ContributionStatus[]) {
		const same =
			this.stageFilter?.length === statuses.length &&
			this.stageFilter.every((s, i) => s === statuses[i]);
		this.stageFilter = same ? null : statuses;
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

	// ---- domain actions ----

	/** Adviser/ops categorises a contribution (sets type/net-gross/tax-year). Triggers auto-match. */
	categorise(id: number, patch: Partial<NewContributionInput> = {}) {
		const c = this.contributions.find((x) => x.id === id);
		if (!c) return;
		if (patch.type) c.type = patch.type;
		if (patch.freq) c.freq = patch.freq;
		if (patch.source) c.source = patch.source;
		if (patch.taxYear) c.taxYear = patch.taxYear;
		c.categorised = true;
		if (c.status === 'identified' || c.status === 'awaiting_confirmation') c.status = 'confirmed';
		this.#notify(`Categorised ${c.member}`, 'expectation.categorise');
		this.tryAutoMatch(id);
	}

	/** Auto-match a categorised, unmatched contribution to an exact account+amount SEI credit. */
	tryAutoMatch(id: number) {
		const c = this.contributions.find((x) => x.id === id);
		if (!c || !c.categorised || c.matchId) return;
		const tx = this.seiPool.find(
			(t) => t.matchedContributionId === null && t.seiAccount === c.seiAccount && t.amount === c.net
		);
		if (!tx) return;
		const match: Match = {
			id: `M-${++this.#matchSeq}`,
			contributionId: c.id,
			seiTransactionId: tx.id,
			origin: 'auto',
			status: 'auto_matched',
			proposedAt: c.date
		};
		this.matches.push(match);
		tx.matchedContributionId = c.id;
		c.matchId = match.id;
		c.expectationId ??= `EXP-${c.id}`;
		this.#notify(`Auto-matched ${c.member} → ${tx.id}`, 'expectation.match');
	}

	/** Create a contribution independently of any payment; optionally request a match (needs approval). */
	createContribution(input: NewContributionInput, matchTxId?: string): number {
		const id = Math.max(0, ...this.contributions.map((c) => c.id)) + 1;
		const c: Contribution = {
			id,
			member: input.member,
			seiAccount: input.seiAccount,
			scheme: input.scheme,
			date: 'today',
			net: input.net,
			type: input.type,
			freq: input.freq,
			source: input.source,
			taxYear: input.taxYear,
			status: 'awaiting_confirmation',
			earnings: Math.max(input.net, 60000),
			age: 50,
			protection: false,
			categorised: false,
			expectationId: null,
			matchId: null
		};
		this.contributions.push(c);
		this.#notify(`Recorded contribution for ${c.member}`, 'Create Pension Contribution');
		if (matchTxId) this.matchOnCreation(id, matchTxId);
		return id;
	}

	/** Adviser opts to match to an existing credit at creation — held for ops approval. */
	matchOnCreation(id: number, txId: string) {
		const c = this.contributions.find((x) => x.id === id);
		const tx = this.seiPool.find((t) => t.id === txId);
		if (!c || !tx || tx.matchedContributionId !== null) return;
		const match: Match = {
			id: `M-${++this.#matchSeq}`,
			contributionId: id,
			seiTransactionId: txId,
			origin: 'on_creation',
			status: 'pending_approval',
			proposedAt: c.date
		};
		this.matches.push(match);
		c.matchId = match.id; // link, but the SEI tx is NOT applied until approved
		this.#notify('Match submitted. Awaiting operations approval.', 'expectation.match.request');
	}

	approveMatch(matchId: string, by = 'ops') {
		const m = this.matches.find((x) => x.id === matchId);
		if (!m || m.status !== 'pending_approval') return;
		const c = this.contributions.find((x) => x.id === m.contributionId);
		const tx = this.seiPool.find((t) => t.id === m.seiTransactionId);
		if (!c || !tx) return;
		m.status = 'approved';
		m.decidedBy = by;
		tx.matchedContributionId = c.id;
		c.expectationId ??= `EXP-${c.id}`;
		if (c.status === 'identified') c.status = 'awaiting_confirmation';
		this.#notify(`Approved match for ${c.member}`, 'expectation.confirm');
	}

	rejectMatch(matchId: string, by = 'ops') {
		const m = this.matches.find((x) => x.id === matchId);
		if (!m || m.status !== 'pending_approval') return;
		const c = this.contributions.find((x) => x.id === m.contributionId);
		m.status = 'rejected';
		m.decidedBy = by;
		if (c && c.matchId === matchId) c.matchId = null; // tx stays in the pool
		this.#notify('Match rejected. Credit returned to the pool.', 'expectation.reject');
	}

	recordToMl(id: number) {
		const c = this.contributions.find((x) => x.id === id);
		if (!c) return;
		c.status = 'ml_recorded';
		this.#notify(`Recorded ${c.member} to Morgan Lloyd`, 'Create Pension Contribution');
	}
	hold(id: number) {
		const c = this.contributions.find((x) => x.id === id);
		if (!c) return;
		c.status = 'awaiting_confirmation';
		this.#notify('On hold. Adviser query raised.', 'zendesk.ticket.update');
	}
	flagManual(id: number) {
		const c = this.contributions.find((x) => x.id === id);
		if (!c) return;
		c.status = 'manual_intervention';
		this.#notify('Flagged for manual intervention', 'reconciliation_breaks.insert');
	}
}

export { PIPELINE };
