import { loginPath } from '@graphene/auth';
import { error, redirect } from '@sveltejs/kit';
import { listingQueryOf } from '$lib/listing-url';
import { clampPage, loadPortalPage } from '$lib/page-data';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, url, parent, depends }) => {
	if (!locals.user) redirect(303, loginPath(url));
	depends('app:composed');

	// Composed-only, like /documents: the tenant's own 'insights' page, or the default set's when
	// they haven't customised (a `news` block in its `library` variant — the whole published
	// library, lead + list, filtered client-side). Insights used to be a hand-built CMS grid this
	// route owned outright and `nav.ts` appended to the tab bar in code; it is now a page in the
	// set, so the studio places, renames or removes it like any other tab, and a firm whose
	// CUSTOM set omits the slug gets a 404 here: they removed the page. `/insights/{uid}` (the
	// reader) stays a built-in route, as `/documents/{uid}` does.
	const { activeContactId } = await parent();
	const composed = await loadPortalPage(
		'insights',
		locals,
		fetch,
		clampPage(url.searchParams.get('page')),
		listingQueryOf(url),
		activeContactId
	);
	if (!composed) error(404, 'Page not found');
	return { composed };
};
