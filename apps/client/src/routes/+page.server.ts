import { loginPath } from '@graphene/auth';
import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, jsonHeaders, publicId } from '$lib/api';
import { listingQueryOf } from '$lib/listing-url';
import { clampPage, loadHomePage } from '$lib/page-data';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url, parent, depends }) => {
	if (!locals.user) redirect(303, loginPath(url));
	depends('app:composed');

	const { activeContactId, onboarding } = await parent();
	// A newly invited client has one job: finish onboarding. Dropping them onto an empty investment
	// dashboard makes the portal look broken and asks them to discover the Onboarding tab for
	// themselves. Keep `/` as the stable post-login destination, but route an active journey to its
	// own guided surface; once the journey finishes this naturally falls through to Home.
	if (onboarding) redirect(303, '/onboarding');
	// Streamed, not awaited: the shell paints and the skeleton holds the widgets' shape while the
	// slices arrive. The slice fetches therefore run AFTER this load has returned, and SvelteKit
	// warns on every `event.fetch` made then because it can no longer be tracked as a dependency.
	// Nothing is lost by using the global fetch here — every slice URL is the absolute API origin
	// with a bearer header, never a relative path or a cookie — and the page's re-run is already
	// pinned by `depends('app:composed')` above, which is how the composed widgets invalidate.
	return {
		composed: loadHomePage(
			locals,
			globalThis.fetch,
			clampPage(url.searchParams.get('page')),
			listingQueryOf(url),
			activeContactId
		)
	};
};

export const actions: Actions = {
	/** Mark one of the client's own action items done (RLS scopes /tasks to their contacts). */
	completeTask: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const uid = publicId(form.get('uid'));
		if (!uid) return fail(400, { error: 'No task specified.' });
		const res = await fetch(`${apiUrl()}/tasks/${uid}/complete`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({})
		});
		if (!res.ok) return fail(res.status, { error: 'We couldn’t complete that. Please try again.' });
		return { ok: true };
	}
};
