// Status vocabularies, tone mapping and pipeline grouping for the workbench.
import type { StatusTone } from '@graphene/ui';
import type { ContributionStatus, MatchStatus } from './types';

/** Canonical pensions.contribution_status order (the lifecycle). */
export const STATUSES: ContributionStatus[] = [
	'identified',
	'awaiting_confirmation',
	'confirmed',
	'ml_recorded',
	'ras_applied',
	'ras_received',
	'tax_relief_received',
	'reconciled'
];

/** Ops-facing canonical labels. */
export const STATUS_LABEL: Record<ContributionStatus, string> = {
	identified: 'Identified',
	awaiting_confirmation: 'Awaiting confirmation',
	confirmed: 'Confirmed',
	ml_recorded: 'Recorded to ML',
	ras_applied: 'RAS applied',
	ras_received: 'RAS received',
	tax_relief_received: 'Relief credited',
	reconciled: 'Reconciled',
	manual_intervention: 'Manual intervention'
};

/** Adviser-friendly labels (platform portal). */
export const ADVISER_LABEL: Record<ContributionStatus, string> = {
	identified: 'Detected',
	awaiting_confirmation: 'Submitted',
	confirmed: 'Categorised',
	ml_recorded: 'Recorded',
	ras_applied: 'Relief claimed',
	ras_received: 'Relief received (ML)',
	tax_relief_received: 'Relief received',
	reconciled: 'Complete',
	manual_intervention: 'Needs attention'
};

/**
 * Semantic-token classes for a status chip (rendered on a `Badge variant="outline"`).
 *
 * Every tone resolves through the theme's derived status tokens, so a tenant's branding
 * cascades here and light/dark both follow from one definition. The mapping is by MEANING:
 * inert → muted, waiting on us → warning, in flight → info, in the Morgan Lloyd pipeline →
 * brand, relief landed → success (subtle), terminal → success (solid), off-ramp → destructive.
 */
/**
 * The `StatusBadge` tone per lifecycle stage.
 *
 * Tone NAMES, not class strings. These were nine hand-written triples
 * (`bg-success-subtle text-success-strong border-success-line` and friends) — a pasted copy of
 * `@graphene/ui`'s own `statusToneClasses`, the drift `field-shell.ts` was written to stop.
 *
 * The lifecycle has more stages than the tone vocabulary has tones, so the late ones share
 * `success` where `reconciled` used to be a solid fill of its own. That is the right way round:
 * the badge always prints the stage's LABEL, and colour reinforces it rather than carrying it —
 * "Reconciled" and "RAS received" are told apart by the words, as the design language requires.
 */
export const STATUS_TONE: Record<ContributionStatus, StatusTone> = {
	identified: 'neutral',
	awaiting_confirmation: 'warning',
	confirmed: 'info',
	ml_recorded: 'brand',
	ras_applied: 'brand',
	ras_received: 'success',
	tax_relief_received: 'success',
	reconciled: 'success',
	manual_intervention: 'danger'
};

export const MATCH_LABEL: Record<MatchStatus, string> = {
	auto_matched: 'Auto-matched',
	pending_approval: 'Pending approval',
	approved: 'Approved',
	rejected: 'Rejected'
};

export const MATCH_TONE: Record<MatchStatus, StatusTone> = {
	auto_matched: 'success',
	pending_approval: 'warning',
	approved: 'success',
	rejected: 'danger'
};

export interface PipelineStage {
	key: string;
	name: string;
	actor: string;
	statuses: ContributionStatus[];
	relief?: boolean;
	manual?: boolean;
}

/** The 7 visible workbench stages (mirrors the HTML prototype pipeline). */
export const PIPELINE: PipelineStage[] = [
	{ key: 'identified', name: 'Identified', actor: 'SEI feed', statuses: ['identified'] },
	{
		key: 'awaiting',
		name: 'Awaiting confirm',
		actor: 'Graphene',
		statuses: ['awaiting_confirmation']
	},
	{ key: 'confirmed', name: 'Classified', actor: 'Graphene', statuses: ['confirmed'] },
	{ key: 'ml_recorded', name: 'Recorded to ML', actor: 'Morgan Lloyd', statuses: ['ml_recorded'] },
	{
		key: 'ras',
		name: 'RAS reclaim',
		actor: 'ML → HMRC',
		statuses: ['ras_applied', 'ras_received'],
		relief: true
	},
	{
		key: 'relief',
		name: 'Relief credited',
		actor: 'ML → SEI',
		statuses: ['tax_relief_received'],
		relief: true,
		manual: true
	},
	{ key: 'reconciled', name: 'Reconciled', actor: 'Graphene', statuses: ['reconciled'] }
];
