<script lang="ts">
	import { Card } from '@graphene/ui/identity-capture-ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Verify your identity</title></svelte:head>

<Card class="w-full gap-3 p-5 text-center">
	<h1 class="text-xl font-semibold">This link has finished</h1>
	<p class="text-muted-foreground text-sm">
		{#if data.ended}
			It works once, and it has already been used or it has expired. Go back to the other device and
			ask for a new one.
		{/if}
	</p>
	<p class="text-sm">
		<a href="/i" class="underline">Continue on this phone</a>
	</p>
</Card>
