import type { TransferStepId } from './stages.js';

/** The path of one footer action, or null when `step` is not one of them.

`STEP_PATH[step]` is the wrong lookup: a plain object inherits `constructor`, and
`Object(uid)` stringifies to the uid itself. Concatenated onto the API origin that is a
userinfo URL, and the BFF then POSTs the session bearer there.
*/
const STEP_SUFFIX: Record<TransferStepId, string> = {
	submit_ml: 'record-to-morgan-lloyd',
	refresh_ml: 'refresh-morgan-lloyd',
	cancel_ml: 'cancel-at-morgan-lloyd',
	place_seccl: 'record-to-seccl',
	refresh_seccl: 'refresh-seccl',
	cancel_seccl: 'cancel-at-seccl',
	lodge_sei: 'record-to-sei',
	submit_sei: 'record-to-sei',
	free_receipt: 'record-to-sei',
	poll_sei: 'refresh-sei'
};

export function transferActionPath(step: string, uid: string): string | null {
	if (!Object.hasOwn(STEP_SUFFIX, step)) return null;
	const path = `/transfers/${encodeURIComponent(uid)}/${STEP_SUFFIX[step as TransferStepId]}`;
	return path.startsWith('/transfers/') ? path : null;
}

/** A `provider_error` on a 200 that means this step was refused.

Refresh from Morgan Lloyd returns the row as it stands. An earlier SEI hold stays
on that row so the panel can still show it, and that string is not this refresh failing.
*/
export function stepRefusal(step: string, providerError: unknown): string | null {
	if (step === 'refresh_ml') return null;
	return typeof providerError === 'string' && providerError ? providerError : null;
}
