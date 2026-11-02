<script lang="ts" module>
	import type { PortalActionItem } from './types.js';

	/**
	 * "Due today" / "Due in N days" / "Overdue" phrasing for an action item's due date — moved
	 * verbatim from apps/client's home page when the card became a portal block. Exported from
	 * the module script so the pure phrasing stays unit-testable without mounting the component.
	 */
	export function dueText(due: string | null): string {
		if (!due) return '';
		const ms = new Date(due + 'T00:00:00').getTime();
		// An unparseable date used to fall through every day-count comparison to 'Overdue'
		// (NaN compares false everywhere) — junk data must not shout red; say nothing instead.
		if (Number.isNaN(ms)) return '';
		const days = Math.round((ms - new Date().setHours(0, 0, 0, 0)) / 86400000);
		if (days === 0) return 'Due today';
		if (days === 1) return 'Due tomorrow';
		if (days > 1) return `Due in ${days} days`;
		return 'Overdue';
	}

	/** True when dueText() phrases the date as overdue (drives the red emphasis). */
	export const overdue = (due: string | null) => dueText(due) === 'Overdue';

	/**
	 * Urgency order: overdue first, then soonest due, undated (or unparseable) last. Applied
	 * BEFORE the block's `limit` cut — unsorted, an overdue item could hide below the cut while
	 * the heading still counted it, i.e. "3 things need your attention" showing none of the
	 * urgent ones. Stable, so equally-urgent items keep the server's order; non-mutating.
	 */
	export function sortActionItems(items: PortalActionItem[]): PortalActionItem[] {
		const key = (t: PortalActionItem): number => {
			if (!t.due_date) return Infinity;
			const ms = new Date(t.due_date + 'T00:00:00').getTime();
			return Number.isNaN(ms) ? Infinity : ms;
		};
		// Explicit zero for equal keys: Infinity − Infinity is NaN, which corrupts a comparator.
		return [...items].sort((a, b) => {
			const ka = key(a);
			const kb = key(b);
			return ka === kb ? 0 : ka - kb;
		});
	}
</script>

<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
	import { Button } from '../ui/button/index.js';
	import * as Card from '../ui/card/index.js';

	// The "N things need your attention" card, ported from apps/client's home page into a portal
	// block. Completion is a callback — the app owns the POST + invalidation — so the same
	// component serves the real portal and the studio preview (which passes interactive=false).
	let {
		items,
		limit,
		hideWhenEmpty = true,
		interactive = true,
		completing = null,
		oncompletetask
	}: {
		items: PortalActionItem[];
		/** Show at most this many rows (the heading still counts them all). */
		limit?: number;
		/** Render nothing when there are no items (the contract's hide_when_empty; default true). */
		hideWhenEmpty?: boolean;
		interactive?: boolean;
		/** The uid currently being marked done — its button shows a pending state. */
		completing?: string | null;
		oncompletetask?: (uid: string) => void;
	} = $props();

	// Sort by urgency before the limit cut so an overdue item can never hide below it; the
	// heading above still counts ALL items, cut or not.
	const shown = $derived.by(() => {
		const sorted = sortActionItems(items);
		return limit !== undefined ? sorted.slice(0, Math.max(0, limit)) : sorted;
	});
	// No handler (or a non-interactive surface) ⇒ the Done buttons render but do nothing.
	const inert = $derived(!interactive || !oncompletetask);
</script>

{#if items.length > 0}
	<Card.Root glass class="h-full">
		<Card.Content class="flex h-full flex-col justify-center">
			<p class="mb-3 flex items-center gap-2 font-semibold">
				<CircleAlertIcon class="text-brand size-4" />
				{items.length === 1 ? 'One thing needs' : `${items.length} things need`} your attention
			</p>
			<div class="space-y-2.5">
				{#each shown as t (t.uid)}
					<div class="flex items-center gap-3">
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-medium">{t.title}</p>
							<p class="text-muted-foreground truncate text-xs">
								{#if t.due_date}
									<span class={overdue(t.due_date) ? 'text-destructive font-medium' : ''}>
										{dueText(t.due_date)}
									</span>
									{#if t.description}·{/if}
								{/if}
								{t.description ?? ''}
							</p>
						</div>
						<Button
							variant="outline"
							size="sm"
							disabled={inert || completing === t.uid}
							onclick={() => oncompletetask?.(t.uid)}
						>
							<CheckIcon class="mr-1 size-3.5" />
							{completing === t.uid ? 'Marking…' : 'Done'}
						</Button>
					</div>
				{/each}
			</div>
		</Card.Content>
	</Card.Root>
{:else if !hideWhenEmpty}
	<Card.Root glass class="h-full">
		<Card.Content class="flex h-full flex-col items-center justify-center gap-1 py-6 text-center">
			<CheckIcon class="text-positive size-5" />
			<p class="text-sm font-medium">Nothing needs your attention</p>
			<p class="text-muted-foreground text-xs">We'll let you know when something does.</p>
		</Card.Content>
	</Card.Root>
{/if}
