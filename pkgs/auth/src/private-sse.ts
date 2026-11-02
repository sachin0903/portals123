export type PrivateSseOptions = {
	fetch: typeof globalThis.fetch;
	apiBaseUrl: string;
	accessToken: string | null;
	ticketPath: string;
	eventsPath: string;
	signal: AbortSignal;
};

const urlFor = (base: string, path: string): string =>
	`${base.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`;

function upstreamError(response: Response): Response {
	const headers = new Headers({ 'cache-control': 'no-store' });
	const contentType = response.headers.get('content-type');
	if (contentType) headers.set('content-type', contentType);
	return new Response(response.body, {
		status: response.status,
		statusText: response.statusText,
		headers
	});
}

/**
 * Exchanges the server-held bearer token for a short-lived ticket, then opens the private API's
 * SSE stream. The ticket stays on this server-to-server hop: it is sent in a header, never returned
 * to the browser or placed in a URL.
 */
export async function privateSseResponse(options: PrivateSseOptions): Promise<Response> {
	const { fetch, apiBaseUrl, accessToken, ticketPath, eventsPath, signal } = options;
	if (!accessToken) {
		return new Response('Not signed in.', {
			status: 401,
			headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' }
		});
	}

	const ticketResponse = await fetch(urlFor(apiBaseUrl, ticketPath), {
		method: 'POST',
		headers: { Authorization: `Bearer ${accessToken}` },
		signal
	});
	if (!ticketResponse.ok) return upstreamError(ticketResponse);

	let ticket: unknown;
	try {
		ticket = ((await ticketResponse.json()) as { ticket?: unknown }).ticket;
	} catch {
		ticket = null;
	}
	if (typeof ticket !== 'string' || ticket.length === 0) {
		return new Response('The realtime ticket response was invalid.', {
			status: 502,
			headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' }
		});
	}

	const eventsResponse = await fetch(urlFor(apiBaseUrl, eventsPath), {
		headers: {
			Accept: 'text/event-stream',
			'x-realtime-ticket': ticket
		},
		signal
	});
	if (!eventsResponse.ok) return upstreamError(eventsResponse);

	return new Response(eventsResponse.body, {
		status: eventsResponse.status,
		headers: {
			'content-type': 'text/event-stream',
			'cache-control': 'no-store, no-transform',
			'x-accel-buffering': 'no'
		}
	});
}
