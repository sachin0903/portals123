<script lang="ts">
	import { afterNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import { committedPagePath } from '@graphene/auth/page-view';

	// A full document load is counted by the server hook. This records the navigations that
	// never request a document: a client-side move that committed onto a successful page.
	// Hover preload does not fire `afterNavigate`. A 404 does, with `page.status` set, and
	// that is not a view. The first paint is `enter`, and counting it here would record
	// the page twice.
	afterNavigate((nav) => {
		const path = committedPagePath(nav, page.status);
		if (!path) return;
		void fetch('/page-view', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ path })
		}).catch(() => {
			// A missed view must not surface on the page.
		});
	});
</script>
