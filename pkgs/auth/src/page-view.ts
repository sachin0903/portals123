/**
 * Record a signed-in portal navigation after the response is on its way.
 *
 * The handle hook counts a successful HTML document only. A client-side navigation is
 * also a `/__data.json` request, and so is a hover preload (`data-sveltekit-preload-data`)
 * and an invalidation refetch — SvelteKit strips the invalidation marker before `handle`,
 * so the server cannot tell those apart. Committed client navigations are recorded from
 * the layout, once `afterNavigate` fires. The body is the pathname only. The call is not
 * awaited — a slow insert must not change the page.
 */

export type PageViewEvent = {
	request: { method: string; headers: { get(name: string): string | null } };
	url: URL;
	isSubRequest: boolean;
	locals: { accessToken: string | null };
};

export function shouldRecordPageView(
	event: PageViewEvent,
	response: { status: number; headers: { get(name: string): string | null } }
): boolean {
	if (event.request.method !== 'GET') return false;
	if (!event.locals.accessToken) return false;
	if (event.isSubRequest) return false;
	if (response.status < 200 || response.status >= 300) return false;
	const type = response.headers.get('content-type') ?? '';
	return type.includes('text/html');
}

/** A client navigation that actually changed page and loaded. The first load is `enter`:
 * the HTML document already counted it. A hover preload never gets this far. Staying on
 * the same pathname — a form that does not leave the page, a query-only change — is not
 * a new view. `afterNavigate` also runs when the move lands on an error page; that status
 * is not a view, matching the document hook, which ignores a non-2xx response. */
export function committedPagePath(
	nav: {
		type: string;
		from?: { url: { pathname: string } } | null;
		to: { url: { pathname: string } } | null;
	},
	status: number
): string | null {
	if (status < 200 || status >= 300) return null;
	if (nav.type === 'enter' || nav.to == null) return null;
	const path = nav.to.url.pathname;
	if (!path.startsWith('/') || path.startsWith('//')) return null;
	if (nav.from?.url.pathname === path) return null;
	return path;
}

export function recordPageView(
	event: PageViewEvent,
	response: { status: number; headers: { get(name: string): string | null } },
	options: { apiUrl: string; fetch?: typeof fetch }
): void {
	if (!shouldRecordPageView(event, response)) return;
	const token = event.locals.accessToken;
	if (!token) return;
	const send = options.fetch ?? globalThis.fetch.bind(globalThis);
	const url = `${options.apiUrl.replace(/\/$/, '')}/me/page-view`;
	void Promise.resolve()
		.then(() =>
			send(url, {
				method: 'POST',
				headers: {
					authorization: `Bearer ${token}`,
					'content-type': 'application/json'
				},
				body: JSON.stringify({ path: event.url.pathname })
			})
		)
		.catch(() => {
			// A failed insert must not change the page, and the path is not logged.
		});
}

/** The layout beacon's forward to the API. The caller already checked the session. */
export async function sendPageView(
	path: string,
	options: { apiUrl: string; accessToken: string; fetch?: typeof fetch }
): Promise<void> {
	const send = options.fetch ?? globalThis.fetch.bind(globalThis);
	const url = `${options.apiUrl.replace(/\/$/, '')}/me/page-view`;
	await send(url, {
		method: 'POST',
		headers: {
			authorization: `Bearer ${options.accessToken}`,
			'content-type': 'application/json'
		},
		body: JSON.stringify({ path })
	});
}

/** Same-origin beacon from a portal layout. Unsigned and malformed bodies record nothing. */
export async function handlePageViewRequest(
	request: { json(): Promise<unknown> },
	options: { apiUrl: string; accessToken: string | null; fetch?: typeof fetch }
): Promise<Response> {
	if (!options.accessToken) return new Response(null, { status: 204 });
	let path = '';
	try {
		const body: unknown = await request.json();
		if (body !== null && typeof body === 'object' && 'path' in body) {
			const raw = body.path;
			if (typeof raw === 'string') path = raw;
		}
	} catch {
		return new Response(null, { status: 400 });
	}
	if (!path.startsWith('/') || path.startsWith('//')) return new Response(null, { status: 400 });
	try {
		await sendPageView(path, {
			apiUrl: options.apiUrl,
			accessToken: options.accessToken,
			fetch: options.fetch
		});
	} catch {
		// A failed insert must not change the page, and the path is not logged.
	}
	return new Response(null, { status: 204 });
}
