/**
 * Account status → StatusBadge tone, in one shared place. The mapping is the legacy client
 * /accounts page's (its inline `statusTone`, re-homed here so the widgets and — next wave — the
 * client app's remaining account surfaces defer to one definition): 'active' reads as success,
 * anything else stays neutral. Status vocabulary is the API's free strings, so unknown values
 * deliberately fall to neutral rather than guessing a severity.
 */
import type { StatusToneName } from '../ui/status-badge/tones.js';
import { sentenceCase } from '../../sentence-case.js';

/** The legacy /accounts mapping: active ⇒ success, everything else ⇒ neutral. */
export function accountStatusTone(status: string): StatusToneName {
	return status.trim().toLowerCase() === 'active' ? 'success' : 'neutral';
}

/**
 * The chip an accounts widget shows for a wrapper, or null for none. Only NON-active statuses
 * chip — an all-active book must not grow a wall of green "active" badges (the widgets' quiet
 * default), and an absent/blank status renders nothing rather than asserting a state.
 */
export function accountStatusChip(
	status: string | null | undefined
): { label: string; tone: StatusToneName } | null {
	const s = status?.trim();
	if (!s || s.toLowerCase() === 'active') return null;
	// The LABEL is named here, not by a `class="capitalize"` at each widget: CSS capitalises every
	// word and cannot touch the separator, so `pre_close` read "Pre_close" and a two-word status
	// came out Title Case beside sentence-case neighbours.
	return { label: sentenceCase(s), tone: accountStatusTone(s) };
}
