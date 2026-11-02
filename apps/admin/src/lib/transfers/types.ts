// Mock domain types for the cross-custodian Transfers workbench prototype.
// Canonical enum values mirror apps/api/migrations/sql/0003_pensions_ledger.sql
// (pensions.transfer_status / transfer_basis / transfer_scope) for fidelity.
// A transfer is a Graphene-owned saga (NOT a transaction); it reconciles down to
// platform.transactions via its expectation. UI prototype — no backend wiring.

/** Canonical pensions.transfer_status saga (authoritative across all routes). */
export type TransferStatus =
	| 'entered'
	| 'submitted_to_ml'
	| 'in_flight'
	| 'ml_complete'
	| 'settlement_date_advised'
	| 'sei_acr_created'
	| 'reconciled'
	| 'adviser_notified'
	| 'manual_intervention';

export type TransferBasis = 'cash' | 'in_specie';
export type TransferScope = 'full' | 'partial';
export type Wrapper = 'SIPP' | 'ISA' | 'JISA' | 'LISA' | 'GIA';

/** The three custody topologies the one workbench serves. */
export type TransferRoute = 'ml_sei' | 'intra_sei' | 'intra_seccl';
export type Custodian = 'sei' | 'seccl';
export type SchemeAdmin = 'ml' | null;

/** Seccl two-level ceding progress (external transferStatus). */
export type CedingProgress =
	'With previous provider' | 'Onboarding assets' | 'Completed' | 'Rejected';

export type BreakType = 'settlement_value_mismatch' | 'ml_submission_failed';
export type BreakStatus = 'open' | 'investigating' | 'resolved' | 'accepted';

/** In-specie asset line (CREST re-registration). */
export interface Asset {
	isin: string;
	name: string;
	qty: number;
	price: number;
	value: number;
	book: number | null; // book cost — null until set; required before CREST settle
	autoBook: boolean;
}

export interface TransferEvent {
	code: string;
	det: string;
	ts: string;
	kind: '' | 'acc' | 'bad' | 'good';
}

export type ReconLegKey = 'graphene' | 'ml' | 'sei' | 'seccl';
export interface ReconLeg {
	key: ReconLegKey;
	label: string;
	value: number;
}
export interface Recon {
	expected: number;
	legs: ReconLeg[];
}

export interface Transfer {
	id: string; // the public uid (a 12-char nanoid; see docs/data-architecture.md Decision 9)
	client: string;
	account: string;
	firm: string;
	firmId: string;

	// route descriptor — drives stage model, applicable ops, and recon shape
	route: TransferRoute;
	custodian: Custodian;
	schemeAdmin: SchemeAdmin;

	provider: string; // ceding provider (e.g. Aviva, Standard Life, or a Seccl/SEI a/c)
	plan: string | null; // ceding plan / policy number
	wrapper: Wrapper;
	value: number;
	basis: TransferBasis;
	scope: TransferScope;

	status: TransferStatus;
	risk: boolean;
	inStage: string; // e.g. '2d'
	created: string;
	settleToday?: boolean;
	actionRequired?: boolean; // in-specie: book cost needs setting

	// scheme-admin leg (ML+SEI route only)
	ml: { scheme: string | null; ref: string | null; settle: string | null } | null;
	// custodian leg (SEI ACR id / Seccl transfer ref)
	custody: { acct: string | null; ref: string | null };
	custodyStatus?: string | null; // raw provider status string (display only)
	cedingProgress?: CedingProgress | null;

	recon: Recon | null;
	breakType?: BreakType | null;
	breakStatus?: BreakStatus | null;

	assets?: Asset[] | null;
	events: TransferEvent[];
}

export interface NewTransferInput {
	client: string;
	account: string;
	firm: string;
	route: TransferRoute;
	provider: string;
	plan: string;
	wrapper: Wrapper;
	value: number;
	basis: TransferBasis;
	scope: TransferScope;
	assets?: Asset[];
}
