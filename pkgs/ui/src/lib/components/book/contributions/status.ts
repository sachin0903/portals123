/** Status vocabularies, tone mapping and pipeline grouping for the contributions workbench.
 *
 *  Adapted from the admin prototype (`apps/admin/src/lib/contributions/status.ts`), which is
 *  where the visual language came from. Two differences, both deliberate:
 *
 *  - the labels are the **adviser-facing** ones, not the ops-facing ones. A firm's paraplanner
 *    does not care that the internal state is `ml_recorded`; they care that it is Recorded.
 *  - tones are semantic tokens, not palette classes. The prototype's hardcoded hues are
 *    unreadable on a dark background AND unreachable by a firm's branding; the token family
 *    resolves per colour scheme, so no `dark:` variant is needed either.
 */
import type { StatusTone } from '../../ui/status-badge/index.js';
import type { ContributionStatus, ExpectationStatus } from './types';

/** The lifecycle, in order. `manual_intervention` is off-pipeline — it can be entered from
 *  anywhere and is not a stage you pass through. */
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

export const STATUS_LABEL: Record<ContributionStatus, string> = {
	identified: 'Detected',
	awaiting_confirmation: 'To classify',
	confirmed: 'Classified',
	ml_recorded: 'Recorded',
	ras_applied: 'Relief claimed',
	ras_received: 'Relief received (ML)',
	tax_relief_received: 'Relief credited',
	reconciled: 'Complete',
	manual_intervention: 'Needs attention',
	cancelled: 'Cancelled'
};

/** Terminal, off-pipeline states. Excluded from the pipeline strip's counts: they are ways a
 *  contribution stops, and totalling them alongside the live stages overstates the work. */
export const OFF_PIPELINE: ReadonlySet<ContributionStatus> = new Set([
	'manual_intervention',
	'cancelled'
]);

/** Tone by meaning: waiting (identified and awaiting confirmation, one stage), in flight, relief
 *  landed, needs rescue. */
export const STATUS_TONE: Record<ContributionStatus, StatusTone> = {
	identified: 'warning',
	awaiting_confirmation: 'warning',
	confirmed: 'info',
	ml_recorded: 'info',
	ras_applied: 'info',
	ras_received: 'success',
	tax_relief_received: 'success',
	reconciled: 'success',
	manual_intervention: 'danger',
	// Neutral, NOT danger: a cancellation is usually a correct decision.
	cancelled: 'neutral'
};

/** The expectation's own lifecycle — whether the custodian has been told. Shown next to the
 *  contribution status because they answer different questions and can disagree: a categorised
 *  contribution whose expectation never reached SEI is a real and visible state. */
export const EXPECTATION_LABEL: Record<ExpectationStatus, string> = {
	expected: 'Not lodged',
	instructed: 'Lodged at SEI',
	matched: 'Matched',
	reconciled: 'Reconciled',
	cancelled: 'Cancelled',
	failed: 'Lodge failed'
};

export const EXPECTATION_TONE: Record<ExpectationStatus, string> = {
	// Neutral, not a warning: "not lodged yet" is the normal state a second after booking.
	expected: 'text-muted-foreground',
	instructed: 'border-info-line text-info-strong',
	matched: 'border-success-line text-success-strong',
	reconciled: 'border-success-line bg-success-subtle text-success-strong',
	cancelled: 'text-muted-foreground',
	failed: 'border-destructive-line text-destructive-strong'
};

/**
 * THE STAGE — one bucket per status, and the ONLY vocabulary the workbench shows.
 *
 * `ContributionStatus` is the engine's word for where a contribution has got to; a STAGE is the
 * word an adviser reads, and there are fewer of them because several statuses answer the same
 * question ("has anybody downstream been told?"). It replaces two vocabularies that had drifted
 * apart — the KPI strip's six ad-hoc reducers and the pipeline strip's seven `PIPELINE` groups —
 * with one list that drives the metrics, the status filter and the panel's action set alike.
 *
 * Each stage names its ACTOR: half of them are waiting on somebody else (Morgan Lloyd, HMRC, the
 * custodian), and knowing which half is the whole point — an adviser looking at "Relief claimed"
 * needs to know those are not theirs to chase.
 *
 * `unit` is what the count is a count OF. Money detected by the feed and money classified here
 * but not yet lodged are counts of TRANSACTIONS (there may be no `platform.contributions` row
 * behind them at all); everything downstream is a count of contributions.
 *
 * It is a total function of `status` alone — deliberately, and mirrored server-side by
 * `app/contributions.py::stage_of` so the filter and the strip cannot disagree. Deriving it from
 * the expectation as well was tried and dropped: an `ml_recorded` row whose SEI expectation had
 * failed would then read as "not lodged", hiding a contribution that IS with the administrator.
 */
export interface ContributionStage {
	key: string;
	label: string;
	/** The caption under the figure: who has it, or which leg the money is on. */
	actor: string;
	statuses: ContributionStatus[];
	unit: 'transactions' | 'contributions';
	/** On the KPI strip. `false` for the terminals, which are a filter answer and not work. */
	metric?: boolean;
}

export const STAGES: ContributionStage[] = [
	{
		key: 'awaiting_classification',
		// "To classify", not "Awaiting classification": the badge sits in a narrow column, and
		// the two words say what the adviser does next. The KEY is unchanged, so a shared URL
		// and the server's `stage_of` still match.
		label: 'To classify',
		actor: 'Custodian feed',
		statuses: ['identified', 'awaiting_confirmation'],
		unit: 'transactions'
	},
	{
		key: 'with_administrator',
		// "Pending relief", not "With scheme administrator". The administrator HOLDING it is a
		// custody fact and not what anyone is waiting on; what the stage means to a reader is that
		// the basic-rate reclaim has not been filed yet. The KEY is unchanged, so the URL a filtered
		// queue is shared as keeps working and the server's `stage_of` needs no edit.
		label: 'Pending relief',
		actor: 'Morgan Lloyd',
		statuses: ['ml_recorded'],
		unit: 'contributions'
	},
	{
		key: 'relief_claimed',
		label: 'Relief claimed',
		actor: 'Morgan Lloyd → HMRC',
		statuses: ['ras_applied'],
		unit: 'contributions'
	},
	{
		key: 'relief_in_transit',
		label: 'Relief in transit',
		actor: 'HMRC → Morgan Lloyd',
		statuses: ['ras_received'],
		unit: 'contributions'
	},
	{
		key: 'relief_credited',
		label: 'Relief credited',
		actor: 'Morgan Lloyd → custodian',
		statuses: ['tax_relief_received'],
		unit: 'contributions'
	},
	{
		// Classified here and nothing downstream knows: the Morgan Lloyd push has not happened
		// (`confirmed`), or the contribution has stalled and needs a human (`manual_intervention`).
		// Since classifying now records to the administrator in the same act, a row sitting here is
		// a FAILED push rather than a step somebody has not got round to — which is why it is a
		// metric at all.
		key: 'not_lodged',
		label: 'Not lodged',
		actor: 'Unclassified',
		statuses: ['confirmed', 'manual_intervention'],
		unit: 'transactions'
	},
	// Filter answers, not work. A completed contribution is not something to do anything about,
	// and a cancelled one is usually a correct decision — counting either makes a tidy book look
	// busy, which is the same reason `OFF_PIPELINE` exists.
	{
		key: 'completed',
		label: 'Completed',
		actor: '—',
		statuses: ['reconciled'],
		unit: 'contributions',
		metric: false
	},
	{
		key: 'cancelled',
		label: 'Cancelled',
		actor: '—',
		statuses: ['cancelled'],
		unit: 'contributions',
		metric: false
	}
];

/** The six the KPI strip shows, in order. */
export const METRIC_STAGES: ContributionStage[] = STAGES.filter((s) => s.metric !== false);

const STAGE_BY_STATUS: Record<string, ContributionStage> = Object.fromEntries(
	STAGES.flatMap((stage) => stage.statuses.map((status) => [status, stage]))
);

/** The stage a status belongs to. Total over `ContributionStatus` — pinned by a test, because a
 *  status with no stage would silently vanish from every metric AND from the filter. */
export const stageOf = (status: ContributionStatus): ContributionStage | null =>
	STAGE_BY_STATUS[status] ?? null;

export const stageKeyOf = (status: ContributionStatus): string | null =>
	stageOf(status)?.key ?? null;

/** Statuses an adviser may still reclassify from. Past `confirmed` the classification has been
 *  acted on downstream, and the API refuses (409) — so the panel must not offer it either. */
export const CLASSIFIABLE: ReadonlySet<ContributionStatus> = new Set([
	'identified',
	'awaiting_confirmation',
	'confirmed'
]);

/** Statuses that mean "somebody has to do something". Drives the Action required count. */
export const NEEDS_ACTION: ReadonlySet<ContributionStatus> = new Set([
	'identified',
	'awaiting_confirmation',
	'manual_intervention'
]);

/** Morgan Lloyd's own status, and what it lets you do.
 *
 *  `cam_updatecontributionapplication` is documented as valid ONLY at Application — so this is
 *  what gates the "Amend at Morgan Lloyd" control. The API re-checks with the vendor before
 *  sending (the mirror is only as fresh as the last refresh), but offering a button that is
 *  going to 409 is its own kind of wrong. */
export const ML_AMENDABLE_STATUS = 809130000;

/** Past here a basic-rate reclaim is in flight at HMRC, and the API refuses to cancel (409):
 *  stopping our record without reversing the reclaim leaves the two disagreeing. The panel hides
 *  the control rather than offering one that is going to fail. */
export const RELIEF_CLAIMED: ReadonlySet<ContributionStatus> = new Set([
	'ras_applied',
	'ras_received',
	'tax_relief_received',
	'reconciled'
]);

export const ML_STATUS_TONE: Record<number, StatusTone> = {
	809130000: 'warning', // Application — still ours to amend
	809130001: 'info', // Active — reclaim running
	809130002: 'neutral', // Stopped
	809130003: 'success' // Received
};
