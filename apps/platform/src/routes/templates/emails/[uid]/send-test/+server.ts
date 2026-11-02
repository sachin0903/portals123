import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, jsonHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';
import { readAudience } from '@graphene/ui/email-editor';

/**
 * Mail the editor's CURRENT content to the SIGNED-IN AUTHOR'S OWN address — the inbox test a browser
 * preview cannot be (Outlook's tables, dark-mode inversion, blocked images, whether the preheader
 * surfaces). Proxied so the access token stays server-side; the browser posts only the live canvas
 * subject/preheader/html/css, exactly as the sibling `/preview` route does.
 *
 * **No recipient crosses this boundary.** The API resolves the address from the caller's own session
 * and ignores any address in the body (`SendTestEmail`), so this handler forwards the same fields
 * `/preview` does and nothing else — an endpoint that accepted an address would let a member of
 * staff aim the firm's verified sending identity, with arbitrary HTML on it, at a client.
 *
 * `audience` MUST BE FORWARDED. It picks which of the firm's two brands — and which sender identity —
 * dresses the mail: a send-time fact the template itself does not carry, which is exactly why the API
 * takes it as a field (`PreviewEmail.audience` in `app/routers/email_templates.py`). This handler
 * builds the body key by key, so omitting it leaves the API's default of `"client"` the only value
 * that can ever arrive — and a task notice (`task_assigned`/`task_reminder`), which really sends
 * with `portal="platform"`
 * (`app/tasks/notifier.py`), then goes out in the wrong brand from the wrong sender, in the very
 * white-label seam this test send exists to check. Normalised to the two values the API's `Audience`
 * literal accepts, so an unrecognised one is a client render, not a 422.
 *
 * Gated on `email_templates:write`, not `:read`: this spends the firm's real email quota and puts
 * mail on the wire, which is the author's permission and not a viewer's — the API requires the same.
 */
export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'email_templates:write')) error(403, 'Not permitted');

	const body = await request.json().catch(() => ({}));
	const res = await fetch(`${apiUrl()}/email-templates/${params.uid}/send-test`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({
			subject: String(body.subject ?? ''),
			preheader: String(body.preheader ?? ''),
			html: String(body.html ?? ''),
			css: String(body.css ?? ''),
			// Which of the firm's two brands dresses it — see this handler's header.
			audience: readAudience(body.audience)
		})
	});
	// The throttle is passed through as a response rather than thrown: `error()` cannot carry a
	// header, and `Retry-After` is what lets the toast name the wait instead of saying "shortly". The
	// body keeps SvelteKit's `{ message }` shape so the client parses both paths identically.
	if (res.status === 429) {
		const retryAfter = res.headers.get('retry-after');
		return json(
			{ message: await detail(res) },
			{ status: 429, headers: retryAfter ? { 'retry-after': retryAfter } : {} }
		);
	}
	if (!res.ok) error(res.status, await detail(res));
	return json(await res.json());
};
