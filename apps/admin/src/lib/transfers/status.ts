// Status vocabulary, tone/bucket mapping, and the per-route stage models.
// The canonical TransferStatus enum is authoritative; each route defines an
// ordered STAGE model (subset + provider-specific labels) normalised onto it,
// straight from the docs/transfers-status-map.html crosswalk.
import type { StatusTone } from '@graphene/ui';
import type { BreakStatus, BreakType, ReconLegKey, TransferRoute, TransferStatus } from './types';

export const STATUSES: TransferStatus[] = [
	'entered',
	'submitted_to_ml',
	'in_flight',
	'ml_complete',
	'settlement_date_advised',
	'sei_acr_created',
	'reconciled',
	'adviser_notified'
];

export type Bucket = 'idle' | 'live' | 'done' | 'break';

/** Ops-facing canonical labels. */
export const STATUS_LABEL: Record<TransferStatus, string> = {
	entered: 'Draft',
	submitted_to_ml: 'Submitted',
	in_flight: 'In flight',
	ml_complete: 'Ceding complete',
	settlement_date_advised: 'Settlement advised',
	sei_acr_created: 'Cash receipt',
	reconciled: 'Reconciled',
	adviser_notified: 'Closed',
	manual_intervention: 'Needs attention'
};

/** Adviser-friendly labels (platform portal). */
export const ADVISER_LABEL: Record<TransferStatus, string> = {
	entered: 'Draft',
	submitted_to_ml: 'Submitted',
	in_flight: 'In progress',
	ml_complete: 'In progress',
	settlement_date_advised: 'Settlement advised',
	sei_acr_created: 'Awaiting funds',
	reconciled: 'Reconciled',
	adviser_notified: 'Complete',
	manual_intervention: 'Needs attention'
};

export const STATUS_BUCKET: Record<TransferStatus, Bucket> = {
	entered: 'idle',
	submitted_to_ml: 'live',
	in_flight: 'live',
	ml_complete: 'live',
	settlement_date_advised: 'live',
	sei_acr_created: 'live',
	reconciled: 'done',
	adviser_notified: 'done',
	manual_intervention: 'break'
};

/**
 * The `StatusBadge` tone per bucket — inert → neutral, in motion → info, terminal → success,
 * off-ramp → danger.
 *
 * A tone NAME, not a class string. This was four hand-written triples
 * (`bg-success-subtle text-success-strong border-success-line` and friends) — which is a pasted
 * copy of `@graphene/ui`'s own `statusToneClasses`, the drift `field-shell.ts` was written to
 * stop. The badge still resolves to those same derived tokens, so a tenant's branding drives it
 * and dark mode follows, but there is one definition of what a success chip looks like.
 */
export const BUCKET_TONE: Record<Bucket, StatusTone> = {
	idle: 'neutral',
	live: 'info',
	done: 'success',
	break: 'danger'
};

export const statusTone = (s: TransferStatus): StatusTone => BUCKET_TONE[STATUS_BUCKET[s]];

/** statuses actively in motion (poll-able / live bucket). */
export const LIVE: Set<TransferStatus> = new Set([
	'submitted_to_ml',
	'in_flight',
	'ml_complete',
	'settlement_date_advised',
	'sei_acr_created'
]);

export interface StageDef {
	status: TransferStatus;
	label: string;
	provider: string;
}

export interface RouteDef {
	key: TransferRoute;
	label: string;
	custodian: 'sei' | 'seccl';
	schemeAdmin: 'ml' | null;
	reconLegs: ReconLegKey[];
	/** ordered display rail (canonical statuses relabelled per provider vocabulary) */
	stages: StageDef[];
	/** canonical status that the recon step runs from (custody leg settled) */
	reconFrom: TransferStatus;
}

export const ROUTES: Record<TransferRoute, RouteDef> = {
	ml_sei: {
		key: 'ml_sei',
		label: 'ML + SEI (SIPP)',
		custodian: 'sei',
		schemeAdmin: 'ml',
		reconLegs: ['graphene', 'ml', 'sei'],
		reconFrom: 'sei_acr_created',
		stages: [
			{ status: 'entered', label: 'Draft', provider: 'Adviser' },
			{ status: 'submitted_to_ml', label: 'Submitted', provider: 'Morgan Lloyd' },
			{ status: 'in_flight', label: 'In flight', provider: 'Morgan Lloyd' },
			{ status: 'ml_complete', label: 'Ceding complete', provider: 'Morgan Lloyd' },
			{ status: 'settlement_date_advised', label: 'Settlement advised', provider: 'Morgan Lloyd' },
			{ status: 'sei_acr_created', label: 'Cash receipt', provider: 'SEI' },
			{ status: 'reconciled', label: 'Reconciled', provider: 'Graphene' }
		]
	},
	intra_sei: {
		key: 'intra_sei',
		label: 'SEI only',
		custodian: 'sei',
		schemeAdmin: null,
		reconLegs: ['graphene', 'sei'],
		reconFrom: 'sei_acr_created',
		stages: [
			{ status: 'entered', label: 'Draft', provider: 'Adviser' },
			{ status: 'submitted_to_ml', label: 'Submitted', provider: 'SEI' },
			{ status: 'in_flight', label: 'In process', provider: 'SEI' },
			{ status: 'sei_acr_created', label: 'Settled', provider: 'SEI' },
			{ status: 'reconciled', label: 'Reconciled', provider: 'Graphene' }
		]
	},
	intra_seccl: {
		key: 'intra_seccl',
		label: 'Seccl only',
		custodian: 'seccl',
		schemeAdmin: null,
		reconLegs: ['graphene', 'seccl'],
		reconFrom: 'ml_complete',
		stages: [
			{ status: 'entered', label: 'Draft', provider: 'Adviser' },
			{ status: 'submitted_to_ml', label: 'Placed · with provider', provider: 'Seccl' },
			{ status: 'in_flight', label: 'Onboarding assets', provider: 'Seccl' },
			{ status: 'ml_complete', label: 'Completed', provider: 'Seccl' },
			{ status: 'reconciled', label: 'Reconciled', provider: 'Graphene' }
		]
	}
};

/**
 * The break vocabularies, named rather than title-cased.
 *
 * `humanise` would render `ml_submission_failed` as "Ml submission failed": ML is Morgan Lloyd,
 * and a generic title-caser cannot know that. Naming them here also keeps the FILTER and the
 * COLUMN reading the same word for the same row, which they did not while one showed the raw
 * slug and the other a humanised one.
 */
export const BREAK_TYPE_LABEL: Record<BreakType, string> = {
	settlement_value_mismatch: 'Settlement value mismatch',
	ml_submission_failed: 'ML submission failed'
};

export const BREAK_STATUS_LABEL: Record<BreakStatus, string> = {
	open: 'Open',
	investigating: 'Investigating',
	resolved: 'Resolved',
	accepted: 'Accepted'
};

export const ROUTE_LABEL: Record<TransferRoute, string> = {
	ml_sei: 'ML + SEI',
	intra_sei: 'SEI',
	intra_seccl: 'Seccl'
};

/**
 * A route badge is categorical (which custody topology), not a status — there is no
 * success/warning meaning to carry. The theme has no categorical series scale yet, so the
 * three routes borrow three branded token families purely to stay distinguishable; swap for
 * a `--series-n` scale if one lands.
 */
export const ROUTE_TONE: Record<TransferRoute, string> = {
	ml_sei: 'bg-brand-subtle text-brand-strong border-brand-line',
	intra_sei: 'bg-info-subtle text-info-strong border-info-line',
	intra_seccl: 'bg-muted text-muted-foreground border-border'
};

export const RECON_LEG_LABEL: Record<ReconLegKey, string> = {
	graphene: 'Graphene',
	ml: 'Morgan Lloyd',
	sei: 'SEI',
	seccl: 'Seccl'
};
