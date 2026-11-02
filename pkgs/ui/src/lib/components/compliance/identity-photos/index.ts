// A client's identity photos as tiles of one size, and the check's analysis under them, for the
// AML console and the firm's compliance tab alike.
export { default as IdentityPhotoGrid, type IdentityPhoto } from './identity-photo-grid.svelte';
export { default as IdentityAnalysisView } from './identity-analysis-view.svelte';
export {
	OUTCOME_STYLES,
	hasAnalysis,
	outcomeKind,
	scorePercent,
	type IdentityAnalysis,
	type IdentityOutcome,
	type OutcomeKind
} from './identity-analysis.js';
