import { loginPath } from '@graphene/auth';
import { error, redirect } from '@sveltejs/kit';
import { listingQueryOf } from '$lib/listing-url';
import { clampPage, loadPortalPage } from '$lib/page-data';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, url, parent, depends }) => {
	if (!locals.user) redirect(303, loginPath(url));
	depends('app:composed');

	// Composed-only: the tenant's own 'documents' page, or the default set's when they haven't
	// customised (loadPortalPage falls back to the defaults for a null/empty pages set — this
	// route always renders for an un-customised tenant; the default documents page carries the
	// toolbar, category pills, unread note and the outstanding-requests banner). A firm whose
	// CUSTOM set deliberately omits the slug gets a 404: they removed the page.
	// Reading `?page=`/`?q=`/`?account=` off `url` is load-bearing: SvelteKit tracks the
	// searchParams access, so pager links and the toolbars' URL round-trip re-run this load.
	const { activeContactId } = await parent();
	const composed = await loadPortalPage(
		'documents',
		locals,
		fetch,
		clampPage(url.searchParams.get('page')),
		listingQueryOf(url),
		activeContactId
	);
	if (!composed) error(404, 'Page not found');
	return { composed };
};
