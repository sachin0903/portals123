// Mock domain types for the Contributions workbench prototype.
// Enum values/labels mirror the canonical schema (apps/api/migrations/sql/0003_pensions_ledger.sql)
// for fidelity — this is a UI prototype with no backend wiring.

export type ContributionStatus =
	| 'identified'
	| 'awaiting_confirmation'
	| 'confirmed'
	| 'ml_recorded'
	| 'ras_applied'
	| 'ras_received'
	| 'tax_relief_received'
	| 'reconciled'
	| 'manual_intervention';

export type ContributionType = 'personal' | 'employer';
export type ContributionFrequency = 'one_off' | 'regular';
export type ContributionSource = 'Client' | 'Third party' | 'Employer';

/** platform.transaction_expectations.status flow (mirrored for labels). */
export type ExpectationStatus =
	'expected' | 'instructed' | 'matched' | 'reconciled' | 'cancelled' | 'failed';

export type MatchOrigin = 'auto' | 'on_creation';
export type MatchStatus = 'auto_matched' | 'pending_approval' | 'approved' | 'rejected';

/** An incoming cash credit observed at SEI, awaiting allocation to a contribution. */
export interface SeiTransaction {
	id: string; // e.g. 'SEITX-0001'
	seiAccount: string; // 'USB0042817'
	amount: number; // net credit landed
	valueDate: string;
	reference: string;
	matchedContributionId: number | null; // null = still in the unmatched pool
}

/** Link between a contribution and a SEI transaction. Orthogonal to contribution status. */
export interface Match {
	id: string;
	contributionId: number;
	seiTransactionId: string;
	origin: MatchOrigin;
	status: MatchStatus;
	proposedAt: string;
	decidedBy?: string;
}

export interface Contribution {
	id: number;
	member: string;
	seiAccount: string; // member's destination SEI a/c
	scheme: string; // SR-xxxxxx
	date: string;
	net: number;
	type: ContributionType;
	freq: ContributionFrequency;
	source: ContributionSource;
	taxYear: string;
	status: ContributionStatus;
	earnings: number;
	age: number;
	protection: boolean;
	/** Flexibly accessed benefits (`crm.contacts.flexi_access_taken`), so the MPAA applies. */
	flexiAccessTaken?: boolean | null;
	categorised: boolean; // adviser has set type/net-gross/tax-year — precondition for auto-match
	expectationId: string | null; // null = exists independently of any SEI payment
	matchId: string | null; // link to a Match, if any
}

export interface RasStep {
	state: 'done' | 'cur' | 'wait';
	title: string;
	meta?: string;
}

export interface RasBatch {
	period: string;
	state: ContributionStatus;
	count: number;
	relief: number;
	current?: boolean;
	accruing?: boolean;
	steps: RasStep[];
}

export interface ReconBreak {
	subject: string;
	member: string;
	scope: string;
	type: string;
	detail: string;
	ageDays: number;
	status: 'open' | 'investigating' | 'resolved' | 'accepted';
}
