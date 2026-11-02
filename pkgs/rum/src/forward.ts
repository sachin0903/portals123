/**
 * Same-origin `/rum` beacon, forwarded to the API with the session bearer.
 *
 * The browser posts here and nowhere else. A missing session records nothing. The body
 * is not logged.
 */

const MAX_BODY = 4096;

export async function handleRumRequest(
	request: { text(): Promise<string> },
	options: { apiUrl: string; accessToken: string | null; fetch?: typeof fetch }
): Promise<Response> {
	if (!options.accessToken) return new Response(null, { status: 204 });
	let raw = '';
	try {
		raw = await request.text();
	} catch {
		return new Response(null, { status: 204 });
	}
	if (raw.length > MAX_BODY) return new Response(null, { status: 204 });
	let traceId = '';
	let spanId = '';
	try {
		const body: unknown = JSON.parse(raw);
		if (body !== null && typeof body === 'object') {
			const record = body as Record<string, unknown>;
			if (typeof record.trace_id === 'string') traceId = record.trace_id;
			if (typeof record.span_id === 'string') spanId = record.span_id;
		}
	} catch {
		return new Response(null, { status: 204 });
	}
	const headers: Record<string, string> = {
		authorization: `Bearer ${options.accessToken}`,
		'content-type': 'application/json'
	};
	if (/^[0-9a-f]{32}$/.test(traceId) && /^[0-9a-f]{16}$/.test(spanId)) {
		headers.traceparent = `00-${traceId}-${spanId}-00`;
	}
	const send = options.fetch ?? globalThis.fetch.bind(globalThis);
	try {
		await send(`${options.apiUrl.replace(/\/$/, '')}/me/rum`, {
			method: 'POST',
			headers,
			body: raw
		});
	} catch {
		// The page has already been served. Do not log the body.
	}
	return new Response(null, { status: 204 });
}
