import type { ContactHoldings } from './holdings.js';

/**
 * Keep the last measured figures until the next read answers.
 *
 * Clearing on the way out would flash dashes over a table `Streamed` is still showing. A
 * timeout or a failure replaces them: a dash is the honest "we gave up".
 */
export function watchHoldings(pending: () => Promise<ContactHoldings | null> | null | undefined) {
	let landed = $state.raw<ContactHoldings | null>(null);
	let generation = 0;
	$effect(() => {
		const next = pending();
		if (!next) return;
		const mine = ++generation;
		next.then(
			(value) => {
				if (mine === generation) landed = value;
			},
			() => {
				if (mine === generation) landed = null;
			}
		);
	});
	return {
		get current() {
			return landed;
		}
	};
}
