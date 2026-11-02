/**
 * An identity check's analysis as the identity-check result reports it: outcomes grouped by
 * analysis (face, authenticity, integrity…), scores, facts and specimen links. The AML console and
 * the firm's compliance tab read the same shape and show it the same way.
 */
import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
import CircleMinusIcon from '@lucide/svelte/icons/circle-minus';
import CircleXIcon from '@lucide/svelte/icons/circle-x';

/** One outcome of the analysis: a part of the check and how it ended (`clear`, `attention`…). */
export type IdentityOutcome = { key: string; label: string; outcome: string };

/** The identity check's reasons: outcomes grouped by analysis, scores, facts and links. */
export type IdentityAnalysis = {
	groups: { key: string; label: string; items: IdentityOutcome[] }[];
	scores: { key: string; label: string; value: number }[];
	facts: { key: string; label: string; value: string }[];
	links: { key: string; label: string; urls: string[] }[];
};

export type OutcomeKind = 'clear' | 'attention' | 'rejected' | 'none';

/** How each kind of outcome shows: its icon, the icon's colour and its word. In legend order. */
export const OUTCOME_STYLES: Record<
	OutcomeKind,
	{ icon: typeof CircleCheckIcon; class: string; word: string }
> = {
	clear: { icon: CircleCheckIcon, class: 'text-success', word: 'Clear' },
	attention: { icon: CircleAlertIcon, class: 'text-warning', word: 'Needs attention' },
	rejected: { icon: CircleXIcon, class: 'text-destructive', word: 'Failed' },
	none: { icon: CircleMinusIcon, class: 'text-muted-foreground', word: 'Not processed' }
};

/** How an outcome reads: clear, needs attention, failed, or not processed. */
export function outcomeKind(outcome: string): OutcomeKind {
	switch (outcome.toLowerCase()) {
		case 'clear':
		case 'passed':
			return 'clear';
		case 'attention':
		case 'refer':
		case 'consider':
			return 'attention';
		case 'rejected':
		case 'failed':
		case 'not_clear':
			return 'rejected';
		default:
			return 'none';
	}
}

/** A score as a percentage: given as 0–100, or as a fraction of 1. */
export function scorePercent(value: number): string {
	const percent = value > 0 && value <= 1 && !Number.isInteger(value) ? value * 100 : value;
	return `${Math.round(percent)}%`;
}

/** Whether the analysis has an outcome, a score or a fact to show. */
export function hasAnalysis(analysis: IdentityAnalysis): boolean {
	return !!(analysis.groups.length || analysis.scores.length || analysis.facts.length);
}
