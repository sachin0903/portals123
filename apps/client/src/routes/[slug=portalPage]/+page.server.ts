import { loginPath } from '@graphene/auth';
import { error, redirect } from '@sveltejs/kit';
import { listingQueryOf } from '$lib/listing-url';
import { clampPage, loadPortalPage } from '$lib/page-data';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, fetch, url, parent, depends }) => {
	if (!locals.user) redirect(303, loginPath(url));
	depends('app:composed');

	// The matcher (src/params/portalPage.ts) only vets the slug's shape and the reserved list;
	// whether THIS tenant has such a page is decided here, against their effective page set.
	// Reading `?page=`/`?q=`/`?account=` off `url` is load-bearing: SvelteKit tracks the
	// searchParams access, so the table variants' pager links and the listing toolbars' URL
	// round-trip re-run this load with the new window/query.
	const { activeContactId } = await parent();
	const composed = await loadPortalPage(
		params.slug,
		locals,
		fetch,
		clampPage(url.searchParams.get('page')),
		listingQueryOf(url),
		activeContactId
	);
	if (!composed) error(404, 'Page not found');
	return { composed };
};
