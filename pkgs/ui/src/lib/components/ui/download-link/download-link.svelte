<script lang="ts">
	// A link to a file the server makes when it is asked for (a report, an application PDF), fetched
	// in place on a plain click: the page stays where it is, a toast says the file is being made,
	// and a failure is a toast with the server's reason. No new tab and no bare error page. It stays
	// a link, so a modified click (new tab, save link as) is the browser's own.
	import type { Snippet } from 'svelte';
	import type { HTMLAnchorAttributes } from 'svelte/elements';
	import { toast } from 'svelte-sonner';
	import { fetchExport, saveFile } from '../../book/listing/export-download.js';

	let {
		href,
		name,
		children,
		...rest
	}: Omit<HTMLAnchorAttributes, 'href' | 'children'> & {
		href: string;
		/** What the file is, for the toasts: "Client report". */
		name: string;
		/** The link's content, told whether the file is being made (to show a spinner). */
		children: Snippet<[{ busy: boolean }]>;
	} = $props();

	let busy = $state(false);

	async function start(event: MouseEvent) {
		if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
			return;
		}
		event.preventDefault();
		if (busy) return;
		busy = true;
		const pending = toast.loading(`Preparing ${name}…`);
		try {
			const outcome = await fetchExport(
				href,
				saveFile,
				fetch,
				`${name} could not be made. Try again.`
			);
			if (outcome.kind === 'failed') toast.error(outcome.message, { id: pending });
			else if (outcome.kind === 'saved') toast.success(`${name} downloaded`, { id: pending });
			else {
				toast.dismiss(pending);
				window.location.assign(href);
			}
		} finally {
			busy = false;
		}
	}
</script>

<!-- `data-sveltekit-reload` for the clicks the browser still handles (a new tab): without it
     SvelteKit's client router intercepts, finds no page to render, and the file never arrives. -->
<a {...rest} {href} data-sveltekit-reload aria-busy={busy ? 'true' : undefined} onclick={start}>
	{@render children({ busy })}
</a>
