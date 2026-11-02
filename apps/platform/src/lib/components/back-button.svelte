<script lang="ts">
	import { Button, getPageBack } from '@graphene/ui';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';

	let {
		fallback,
		fallbackLabel,
		class: className = ''
	}: {
		/** Where Back goes when there is no history to consult — the section's list. */
		fallback: string;
		/** Parent name for the default label, e.g. "forms" → "Back to forms". */
		fallbackLabel: string;
		class?: string;
	} = $props();

	// Deterministic Back — always a plain link, never history.back(). history.back() loops when you
	// arrive via a non-linear path (e.g. bouncing between a template and its editor); the trail in
	// `$lib/back-stack` collapses those loops instead, so going back from a page you have already
	// been on skips past it rather than returning to it.
	//
	// The target is resolved ONCE, in the root layout, and published through context: where you
	// actually came from, else the origin a link spelt into the URL (`?from=`/`?fromLabel=`, which
	// several template and form links still carry), else the section list below. This control used to
	// read `?from=` for itself, which meant it and the topbar answered the same question from
	// different evidence — and disagreed the moment both were true.
	const dynamic = getPageBack();
	const target = $derived(dynamic?.current ?? { href: fallback, label: fallbackLabel });
</script>

<Button
	variant="ghost"
	size="sm"
	class="text-muted-foreground -ml-2 {className}"
	href={target.href}
>
	<ArrowLeftIcon class="mr-1 size-4" />
	Back to {target.label}
</Button>
