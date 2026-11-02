<script lang="ts">
	import { page } from '$app/state';
	import { Button } from '@graphene/ui';
	import CompassIcon from '@lucide/svelte/icons/compass';

	// A human message for the common cases; fall back to whatever the error carried.
	const message = $derived(
		page.status === 404
			? 'This page doesn’t exist.'
			: page.status === 403
				? 'You don’t have access to this page.'
				: (page.error?.message ?? 'Something went wrong.')
	);
</script>

<svelte:head><title>{page.status} · Platform</title></svelte:head>

<div class="flex min-h-[60svh] flex-col items-center justify-center gap-1 p-6 text-center">
	<CompassIcon class="text-muted-foreground mb-3 size-8" />
	<p class="text-muted-foreground text-sm font-semibold tracking-widest tabular-nums">
		{page.status}
	</p>
	<h1 class="text-2xl font-semibold tracking-tight">{message}</h1>
	<p class="text-muted-foreground text-sm">
		If you followed a link to get here, it may be out of date.
	</p>
	<Button href="/" class="mt-4">Back to dashboard</Button>
</div>
