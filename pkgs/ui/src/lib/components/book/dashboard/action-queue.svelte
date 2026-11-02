<script lang="ts">
	// The rail's "needs you" list. One compact row per item — a kind icon, two lines, and an
	// unread-style dot when the next move is the reader's — ordered by `./action-queue.ts`. The
	// row opens the working surface; the dashboard does not duplicate the journey or the review
	// inline.
	import EmptyState from '../../ui/empty-state/empty-state.svelte';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import RouteIcon from '@lucide/svelte/icons/route';
	import StampIcon from '@lucide/svelte/icons/stamp';
	import type { RailItem } from './action-queue';

	let {
		items,
		limit = 6,
		/** Copy for the all-clear state — the caller knows which sources actually loaded. */
		emptyDescription
	}: {
		items: RailItem[];
		limit?: number;
		emptyDescription: string;
	} = $props();

	const shown = $derived(items.slice(0, limit));

	/** Is the next move the reader's? A review always is; a journey is when a staff step is open.
	 * With the client, or with Graphene, it is somebody else's turn — no dot. */
	const needsYou = (item: RailItem): boolean => item.kind === 'review' || item.ball === 'staff';
</script>

{#if items.length === 0}
	<EmptyState compact icon={CircleCheckIcon} title="All clear" description={emptyDescription} />
{:else}
	<ul class="divide-border divide-y text-xs">
		{#each shown as item (item.key)}
			<li class="hover:bg-accent/40 -mx-2 rounded-md">
				<a href={item.href} class="flex items-center gap-2.5 px-2 py-2">
					<!-- The kind, as an icon rather than a word: a route for a journey, a stamp for a
					     review. The colour carries whose move it is — brand for a review (always
					     yours), info while it is with the client, warning when a staff step is open —
					     so a row reads before the words do. -->
					<span
						class="flex size-6 shrink-0 items-center justify-center rounded-md {item.kind ===
						'review'
							? 'bg-brand/10 text-brand'
							: item.ball === 'staff'
								? 'bg-warning/10 text-warning'
								: 'bg-info/10 text-info'}"
						aria-hidden="true"
					>
						{#if item.kind === 'review'}
							<StampIcon class="size-3.5" />
						{:else}
							<RouteIcon class="size-3.5" />
						{/if}
					</span>
					<span class="min-w-0 flex-1">
						<span class="block truncate font-medium">{item.title}</span>
						<span class="text-muted-foreground block truncate">{item.detail}</span>
					</span>
					{#if needsYou(item)}
						<!-- An unread marker, not a chip: the row already says what it is, the dot
						     says only that the next move is yours. -->
						<span class="bg-warning size-2 shrink-0 rounded-full" title="Needs you">
							<span class="sr-only">Needs you</span>
						</span>
					{/if}
				</a>
			</li>
		{/each}
	</ul>
{/if}
