/**
 * Sequencing a listing's URL changes when ONE CLICK asks for more than one.
 *
 * THE DEFECT. Choosing a status while the clients listing shows the Closed population does two
 * things: it switches population (`onsegment`) and it sets a filter (`onquery`). Each handler
 * built its next URL by reading the CURRENT one — but the first navigation has not written it yet,
 * so the second rebuilt from the stale URL and reinstated `?segment=closed`, winning because it
 * navigates with `replaceState`. The table then showed the live book while the URL still said
 * closed, and the Export button — which is deliberately built from the URL, so the file matches
 * the screen — downloaded the CLOSED set under whatever status the reader had picked.
 *
 * The fix is that both handlers build on what this click has already asked for. `pending` holds
 * that until the navigation lands; after it lands the URL is authoritative again.
 *
 * Pure on purpose: `goto` and `page` are SvelteKit virtuals that `pkgs/ui` cannot import, so the
 * app passes in how it navigates and this owns only the ordering.
 */
export type ListingNav = {
	/** The params the next change must build on — what this click already asked for, or the URL. */
	params(current: URLSearchParams): URLSearchParams;
	/** Navigate to `params`, and hold them as the base for anything else this click asks for. */
	commit(params: URLSearchParams, replaceState?: boolean): void;
};

export function createListingNav(
	go: (search: string, options: { replaceState: boolean }) => unknown
): ListingNav {
	let pending: URLSearchParams | null = null;
	return {
		params(current) {
			return new URLSearchParams(pending ?? current);
		},
		commit(params, replaceState = false) {
			pending = params;
			void Promise.resolve(go(params.toString(), { replaceState })).then(
				// Only when nothing newer has been asked for since: a later click owns the base from
				// then on, and clearing unconditionally would drop what it is still waiting on.
				() => {
					if (pending === params) pending = null;
				},
				() => {
					if (pending === params) pending = null;
				}
			);
		}
	};
}
