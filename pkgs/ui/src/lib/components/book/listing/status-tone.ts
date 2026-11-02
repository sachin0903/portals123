import type { StatusTone } from '../../ui/status-badge/index.js';

/**
 * The one status → tone map for every book listing.
 *
 * Before this there were three `Badge variant` maps (accounts' `STATUS_VARIANT`, the clients
 * listing's own, and transactions inline) and all three said the same wrong thing:
 * `variant="secondary"` for Active. `docs/design-language.md` names that exact mistake — "`Badge
 * variant="secondary"` meaning 'good'" — because `secondary` is a NEUTRAL surface, so a healthy
 * account and a draft one read identically and the column carried no information at a glance.
 *
 * Tones, not variants, and one map rather than one per vocabulary: `clients`, `accounts` and
 * `transactions` each derive their own status words, but "active" has to look the same on all
 * three or the colour means nothing. `statusToneClasses` (status-badge/tones.ts) is still the
 * single place a tone becomes a colour; this only decides which tone a WORD gets.
 *
 * `neutral` is the deliberate default for an unknown word, never a guess: a status we do not
 * recognise is not "probably fine", and tinting it green would state something we do not know.
 */
const TONES: Record<string, StatusTone> = {
	// Healthy, settled, done.
	active: 'success',
	settled: 'success',
	completed: 'success',
	fulfilled: 'success',
	// A client the firm has taken on but who holds no account yet — a lead. `brand`, not `info`:
	// this is a stage of OUR pipeline rather than a process state, and the firm's own hue says
	// "ours, not yet a client" without spending amber (reserved for "needs attention") or blue
	// (which every in-flight process state below already uses). It is also the one tone that
	// follows a firm's branding, which is right for the status a firm sees most on a book it is
	// still building.
	lead: 'brand',
	// The custodian has paused the person. Amber, not red: this is a hold, not a failure, and
	// Closed already spends `neutral` on an ending that is fine.
	suspended: 'warning',
	// In flight — something is expected to happen next. `info` rather than `warning`: waiting is
	// normal, and reserving amber for "needs attention" is what keeps amber worth noticing.
	pending: 'info',
	invited: 'info',
	queued: 'info',
	generating: 'info',
	draft: 'info',
	in_progress: 'info',
	// Needs a human.
	pending_action: 'warning',
	blocked: 'warning',
	needs_review: 'warning',
	// A payment is booked and waiting for a second person. Amber: nothing is sent until they decide.
	awaiting_approval: 'warning',
	// Released, and the custodian has not taken it yet. Waiting, not done.
	approved: 'info',
	// Ended badly, or ended deliberately.
	failed: 'danger',
	cancelled: 'danger',
	rejected: 'danger',
	// Ended, and that is fine. Not `danger`: a closed account and a failed payment are not the same
	// news, and colouring both red teaches an adviser to ignore red.
	closed: 'neutral',
	inactive: 'neutral',
	archived: 'neutral',
	superseded: 'neutral',
	deleted: 'neutral'
};

/** The tone a status word carries. Unknown words are `neutral` — never a guess. */
export function statusTone(status: string | null | undefined): StatusTone {
	if (!status) return 'neutral';
	return TONES[status.trim().toLowerCase()] ?? 'neutral';
}
