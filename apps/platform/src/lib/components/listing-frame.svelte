<script lang="ts">
	import { Card, CardContent } from '@graphene/ui';
	import type { Snippet } from 'svelte';

	/** A listing page: it fills the viewport under the topbar from md, and its glass card scrolls.
	 *  `banner` renders above the card (load errors, notices). */
	let { banner, children }: { banner?: Snippet; children: Snippet } = $props();
</script>

<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{@render banner?.()}
	<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
		<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
			{@render children()}
		</CardContent>
	</Card>
</div>
