<script module lang="ts">
	type NotificationQuery = {
		q: string;
		category: string[];
		priority: string[];
		state: string[];
		since: string;
		until: string;
	};

	/** The toolbar's answer written back to the URL, over the CURRENT params so a `?focus=` deep
	 *  link survives a filter change. Exported so a test can call it. */
	export function notificationParams(
		base: URLSearchParams,
		next: NotificationQuery
	): URLSearchParams {
		const params = new URLSearchParams(base);
		for (const [key, value] of Object.entries(next)) {
			// Delete first: a cleared filter must LEAVE the URL, and `set` would keep its 2nd value.
			params.delete(key);
			for (const one of Array.isArray(value) ? value : [value]) if (one) params.append(key, one);
		}
		// A narrowed list makes the reader's page number meaningless; back to page one.
		params.delete('page');
		return params;
	}
</script>

<script lang="ts">
	import { NotificationFeed } from '@graphene/ui';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { markNotification } from '$lib/notifications';

	let { data } = $props();

	// `page` was the only param this route paged on before search/filters existed, so a
	// Previous/Next link changes just that one and carries every other param (now including `q`,
	// `category`, `priority`, `state`) forward unchanged — a filtered search must stay filtered
	// across pages, not reset itself the moment you page through it.
	function pageHref(n: number): string {
		const params = new URLSearchParams(page.url.searchParams);
		if (n <= 1) params.delete('page');
		else params.set('page', String(n));
		const qs = params.toString();
		return qs ? `?${qs}` : page.url.pathname;
	}

	function onquery(next: NotificationQuery) {
		const qs = notificationParams(page.url.searchParams, next).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
</script>

<svelte:head><title>Notifications</title></svelte:head>

<div class="p-1 sm:p-2">
	<NotificationFeed
		items={data.notifications}
		hasMore={data.hasMore}
		page={data.page}
		previousHref={data.page > 1 ? pageHref(data.page - 1) : undefined}
		nextHref={data.hasMore ? pageHref(data.page + 1) : undefined}
		query={data.query}
		filters={data.filters}
		facets={data.facets}
		{onquery}
		onMarkRead={(uid) => markNotification(uid)}
		onMarkAllRead={() => markNotification()}
	/>
	<!-- No onDismiss: this page is the archive, and dismiss is the bell's action (it takes a
	     notification off the BELL; it never deletes it) — a × here that hid a row from this page
	     without it ever leaving the underlying record would be a control with no honest description. -->
</div>
