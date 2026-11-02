<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		heading,
		subtext,
		children
	}: {
		heading: string;
		/** Optional supporting line under the heading. */
		subtext?: string;
		children: Snippet;
	} = $props();
</script>

<!-- The shared auth surface: the same liquid-glass card the login form uses, so
     every step in the sign-in flow (login, email verify, MFA, MFA enrolment)
     reads as one consistent card. liquidGL (where enabled) targets `.glass`. -->
<!-- Radius scales off --radius (x2) rather than Tailwind's fixed 3xl step: 1.5rem at the
     0.75rem default (unchanged), and genuinely sharp under the sharp surface preset. -->
<div class="glass space-y-8 rounded-[calc(var(--radius)*2)] p-6 sm:p-8">
	<div>
		<h1 class="text-3xl font-semibold tracking-tight">{heading}</h1>
		{#if subtext}<p class="text-muted-foreground mt-1.5 text-sm">{subtext}</p>{/if}
	</div>
	{@render children()}
</div>
