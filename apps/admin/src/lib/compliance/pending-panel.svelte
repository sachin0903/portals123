<script lang="ts">
	// A check sent to ComplyCube with no result yet: what has happened, what ComplyCube is doing, and
	// what comes next. The page asks for the result again while a check waits, so the result takes
	// this panel's place by itself.
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import CircleDashedIcon from '@lucide/svelte/icons/circle-dashed';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import type { Component } from 'svelte';
	import { sentAgo, waitingLong } from '$lib/aml';

	let {
		sent,
		title,
		icon: Icon,
		checking,
		next
	}: {
		/** When the check went to ComplyCube (ISO). */
		sent: string;
		title: string;
		icon: Component<{ class?: string }>;
		/** What ComplyCube checks, one short phrase each. */
		checking: string[];
		/** What shows here once the result comes back. */
		next: string;
	} = $props();

	let now = $state(Date.now());
	$effect(() => {
		const id = setInterval(() => (now = Date.now()), 15_000);
		return () => clearInterval(id);
	});
	const ago = $derived(sentAgo(sent, now));
	const slow = $derived(waitingLong(sent, now));
</script>

<div class="bg-muted/30 @container rounded-lg border p-4" role="status">
	<div class="flex items-start gap-4">
		<span
			class="bg-info/10 text-info relative flex size-10 shrink-0 items-center justify-center rounded-full"
		>
			<Icon class="size-5" />
			<span class="absolute -top-0.5 -right-0.5 flex size-3" aria-hidden="true">
				<span
					class="bg-info absolute inline-flex size-full animate-ping rounded-full opacity-60 motion-reduce:animate-none"
				></span>
				<span class="bg-info ring-card relative inline-flex size-3 rounded-full ring-2"></span>
			</span>
		</span>
		<div class="min-w-0 space-y-1">
			<p class="font-medium">{title}</p>
			<p class="text-muted-foreground text-xs">
				{#if slow}
					This is taking longer than usual. Graphene keeps asking ComplyCube for the result.
				{:else}
					ComplyCube usually answers within a few minutes.
				{/if}
				This page updates by itself.
			</p>
		</div>
	</div>

	<ol class="mt-4 grid gap-4 border-t pt-4 @lg:grid-cols-3">
		<li class="flex gap-2.5">
			<CircleCheckIcon class="text-success mt-0.5 size-4 shrink-0" aria-hidden="true" />
			<div class="min-w-0">
				<p class="font-medium">Sent to ComplyCube</p>
				<p class="text-muted-foreground text-xs">{ago}</p>
			</div>
		</li>
		<li class="flex gap-2.5" aria-current="step">
			<LoaderCircleIcon
				class="text-info mt-0.5 size-4 shrink-0 animate-spin motion-reduce:animate-none"
				aria-hidden="true"
			/>
			<div class="min-w-0">
				<p class="font-medium">Checking</p>
				<ul class="mt-1.5 flex flex-wrap gap-1">
					{#each checking as item (item)}
						<li class="bg-background rounded-full border px-2 py-0.5 text-[0.7rem]">{item}</li>
					{/each}
				</ul>
			</div>
		</li>
		<li class="text-muted-foreground flex gap-2.5">
			<CircleDashedIcon class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
			<div class="min-w-0">
				<p class="font-medium">Result</p>
				<p class="text-xs">{next}</p>
			</div>
		</li>
	</ol>
</div>
