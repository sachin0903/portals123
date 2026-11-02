<script lang="ts">
	import type { Component } from 'svelte';
	import FilePenLineIcon from '@lucide/svelte/icons/file-pen-line';
	import ChartLineIcon from '@lucide/svelte/icons/chart-line';
	import FileIcon from '@lucide/svelte/icons/file';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import LandmarkIcon from '@lucide/svelte/icons/landmark';
	import MailIcon from '@lucide/svelte/icons/mail';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { Badge } from '../ui/badge/index.js';
	import { cn } from '../../utils.js';
	import type { ClientDocument, DocumentCategory } from './types.js';

	let {
		documents,
		grouped = true
	}: {
		documents: ClientDocument[];
		/** Group rows under category headers (overview previews pass false). */
		grouped?: boolean;
	} = $props();

	// Typed by the wire contract, but the server can drift ahead of the client — a category this
	// build has never heard of must still render (generic icon), not vanish from the grouped
	// view and throw in the flat one (an undefined icon component).
	const icons: Record<string, Component> = {
		Agreements: FilePenLineIcon,
		Illustrations: ChartLineIcon,
		Statements: FileTextIcon,
		Tax: LandmarkIcon,
		Correspondence: MailIcon
	};

	// Stable category order for the grouped view.
	const order: DocumentCategory[] = [
		'Statements',
		'Illustrations',
		'Agreements',
		'Tax',
		'Correspondence'
	];

	const groups = $derived.by(() => {
		const known = order
			.map((category) => ({
				category: category as string,
				items: documents.filter((d) => d.category === category)
			}))
			.filter((g) => g.items.length > 0);
		// Anything with an unrecognised category files under 'Other', rendered last — server
		// drift shows up as an odd bucket, never as a silently missing document.
		const other = documents.filter((d) => !(order as readonly string[]).includes(d.category));
		return other.length > 0 ? [...known, { category: 'Other', items: other }] : known;
	});
</script>

{#snippet row(doc: ClientDocument)}
	{@const Icon = icons[doc.category] ?? FileIcon}
	<!-- The size/format details shed by the COMPONENT's container width, not the viewport (the
	     established table-component pattern): the old sm: variants kept them crammed into a
	     narrow grid span on a desktop and hidden in a full-width block on a large phone. @lg
	     (512px) is roughly where name + category + size + badge stop fighting for the row. -->
	<a
		href="/documents/{doc.id}"
		class="hover:bg-accent/40 @lg:gap-4 flex items-center gap-3 px-4 py-3.5 transition-colors first:rounded-t-xl last:rounded-b-xl"
	>
		<span
			class="bg-muted text-muted-foreground flex size-10 shrink-0 items-center justify-center rounded-lg"
		>
			<Icon class="size-5" />
		</span>
		<div class="min-w-0 flex-1">
			<p class="truncate font-medium">{doc.name}</p>
			<p class="text-muted-foreground text-xs">{doc.category} · {doc.date}</p>
		</div>
		<div class="flex shrink-0 items-center gap-3">
			<span class="text-muted-foreground @lg:inline hidden text-xs tabular-nums">{doc.size}</span>
			<Badge variant="outline" class="@lg:inline-flex hidden font-medium">{doc.format}</Badge>
			<ChevronRightIcon class="text-muted-foreground size-4" />
		</div>
	</a>
{/snippet}

<!-- The one size container both views' rows key off (the block's grid span, per portal-page). -->
<div class="@container">
	{#if grouped}
		<div class="space-y-6">
			{#each groups as group (group.category)}
				<section class="space-y-2">
					<h2
						class="text-muted-foreground flex items-center gap-2 px-1 text-xs font-semibold tracking-[0.16em] uppercase"
					>
						{group.category}
						<span class="text-muted-foreground/70 font-normal tracking-normal normal-case">
							· {group.items.length}
						</span>
					</h2>
					<div class="glass-panel divide-y rounded-xl">
						{#each group.items as doc (doc.id)}
							{@render row(doc)}
						{/each}
					</div>
				</section>
			{/each}
		</div>
	{:else}
		<div class={cn('divide-y')}>
			{#each documents as doc (doc.id)}
				{@render row(doc)}
			{/each}
		</div>
	{/if}
</div>
