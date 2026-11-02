/**
 * POST the share action on the current illustrations page.
 *
 * That route also serves `POST` from `+server.ts` (generate). SvelteKit sends a
 * fetch whose Accept is not `text/html` to that endpoint, unless the request
 * names itself as a form action. A bare `fetch('?/share')` never reaches share.
 */
export function postShare(uid: string): Promise<Response> {
	const body = new FormData();
	body.set('uid', uid);
	return fetch('?/share', {
		method: 'POST',
		body,
		headers: { accept: 'application/json', 'x-sveltekit-action': 'true' }
	});
}
