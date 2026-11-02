<script lang="ts">
	import { cn } from '../../../utils.js';
	import { Button } from '../button/index.js';

	/**
	 * A non-blocking banner shown when a page load couldn't fetch some of its data (e.g. a transient
	 * 401 at the auth refresh boundary). Distinguishes "load failed" from "no data" so the page
	 * doesn't render a misleading empty state. `onRetry` should re-run the load (e.g. `invalidateAll`)
	 * — a fresh request lets `hooks.server.ts` refresh an expired token.
	 */
	let {
		message = 'Some information couldn’t be loaded. Try again.',
		retryLabel = 'Retry',
		onRetry,
		class: className
	}: {
		message?: string;
		retryLabel?: string;
		onRetry?: () => void;
		class?: string;
	} = $props();
</script>

<div
	data-slot="load-error-banner"
	role="alert"
	class={cn(
		'border-warning-line bg-warning-subtle flex items-center justify-between gap-3 rounded-lg border px-4 py-3 text-sm',
		className
	)}
>
	<span class="text-warning-strong">{message}</span>
	{#if onRetry}
		<Button variant="outline" size="sm" onclick={onRetry}>{retryLabel}</Button>
	{/if}
</div>
