<script lang="ts">
	import { browser } from '$app/environment';
	import { afterNavigate, beforeNavigate } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import { createRum, watchHidden, type RumPortal } from './session';

	let { portal }: { portal: RumPortal } = $props();

	// `beforeNavigate` and `afterNavigate` are implemented with `onMount`. Calling them from
	// `hooks.client.ts` throws and the hydrated tree is thrown away. They are legal here.
	// `onMount` itself stays unconditional: a lifecycle call inside `if (browser)` does not run.
	const rum =
		browser && typeof navigator.sendBeacon === 'function' ? createRum(() => portal) : null;
	if (rum) {
		rum.startVitals();
		beforeNavigate(() => {
			rum.before();
		});
		afterNavigate((nav) => {
			rum.after(nav, page.data);
		});
		watchHidden(() => {
			rum.hide();
		});
	}
	onMount(() => {
		rum?.ensureEnter(page.route.id, page.data);
	});
</script>
