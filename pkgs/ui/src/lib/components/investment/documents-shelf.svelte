<script lang="ts">
	import type { Component } from 'svelte';
	import FilePenLineIcon from '@lucide/svelte/icons/file-pen-line';
	import ChartLineIcon from '@lucide/svelte/icons/chart-line';
	import FileIcon from '@lucide/svelte/icons/file';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import LandmarkIcon from '@lucide/svelte/icons/landmark';
	import MailIcon from '@lucide/svelte/icons/mail';
	import type { ClientDocument } from './types.js';

	// The 'shelf' documents look — a quiet tile grid, like spines on a shelf rather than rows in
	// a register. Each tile is a big category glyph over a clamped name and a small date/size
	// line. The tiles are deliberately QUIET: hairline border, transparent fill (so the look
	// sits on both the card and ledger page grounds), with a small lift + brand border on hover
	// only when the surface is interactive.
	let {
		documents,
		interactive = true
	}: {
		documents: ClientDocument[];
		/** False renders inert tiles (the studio preview) — no links, no hover affordance. */
		interactive?: boolean;
	} = $props();

	// Same category→glyph mapping as documents-list (kept in step by hand — the map lives in
	// that component's instance script, so it isn't importable), including its drift rule: a
	// category this build has never heard of must still render (generic icon), never vanish.
	const icons: Record<string, Component> = {
		Agreements: FilePenLineIcon,
		Illustrations: ChartLineIcon,
		Statements: FileTextIcon,
		Tax: LandmarkIcon,
		Correspondence: MailIcon
	};
</script>

{#snippet tileInner(doc: ClientDocument)}
	{@const Icon = icons[doc.category] ?? FileIcon}
	<Icon class="text-muted-foreground size-7" aria-hidden="true" />
	<p class="line-clamp-2 text-sm font-medium">{doc.name}</p>
	<p class="text-muted-foreground mt-auto text-xs">{doc.date} · {doc.size}</p>
{/snippet}

<!-- Columns key off the COMPONENT's container width (the block's grid span), not the viewport —
     the established investment-block pattern. 2 across by default, 3 from @2xl, 4 from @4xl. -->
<div class="@container">
	<div class="@2xl:grid-cols-3 @4xl:grid-cols-4 grid grid-cols-2 gap-3">
		{#each documents as doc (doc.id)}
			{#if interactive}
				<!-- The lift is motion-safe-gated; the border colour change alone carries the hover
				     state for reduced-motion users. -->
				<a
					href="/documents/{doc.id}"
					data-shelf-tile
					class="hover:border-brand flex min-h-11 flex-col gap-3 rounded-lg border p-4 transition motion-safe:hover:-translate-y-0.5"
				>
					{@render tileInner(doc)}
				</a>
			{:else}
				<div data-shelf-tile class="flex min-h-11 flex-col gap-3 rounded-lg border p-4">
					{@render tileInner(doc)}
				</div>
			{/if}
		{/each}
	</div>
</div>
