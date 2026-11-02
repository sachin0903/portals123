<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
	import { Button } from '../ui/button/index.js';
	import { dueText, overdue, sortActionItems } from './action-items-card.svelte';
	import type { PortalActionItem } from './types.js';

	// The 'banner' action-items look — one brand-tinted attention panel (the onboarding CTA's
	// border-brand/30 + bg-brand/5 language) instead of a glass card. Items collapse to
	// compact single-line rows: title + due phrase + the Done affordance. The behavioural
	// contract is action-items-card's, via its own module exports: sortActionItems runs
	// BEFORE the limit cut (an overdue item can never hide below it), the heading counts ALL
	// items, dueText/overdue keep the NaN-safe phrasing, and hide-when-empty semantics are
	// identical.
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

	const shown = $derived.by(() => {
		const sorted = sortActionItems(items);
		return limit !== undefined ? sorted.slice(0, Math.max(0, limit)) : sorted;
	});
	// No handler (or a non-interactive surface) ⇒ the Done buttons render but do nothing.
	const inert = $derived(!interactive || !oncompletetask);
</script>

{#if items.length > 0}
	<div
		class="border-brand/30 bg-brand/5 flex h-full flex-col justify-center rounded-xl border px-4 py-3"
	>
		<p class="flex items-center gap-2 text-sm font-semibold">
			<CircleAlertIcon class="text-brand size-4 shrink-0" aria-hidden="true" />
			{items.length === 1 ? 'One thing needs' : `${items.length} things need`} your attention
		</p>
		<div class="divide-brand/15 mt-1 divide-y">
			{#each shown as t (t.uid)}
				<div class="flex min-h-11 items-center gap-3 py-1">
					<p class="min-w-0 flex-1 truncate text-sm">
						<span class="font-medium">{t.title}</span>
						{#if t.due_date && dueText(t.due_date)}
							<span
								class={overdue(t.due_date)
									? 'text-destructive font-medium'
									: 'text-muted-foreground'}
							>
								· {dueText(t.due_date)}
							</span>
						{/if}
					</p>
					<!-- min-h-11 overrides the sm button's fixed h-7 (min-height wins) so the tap
					     target stays ≥44px without inflating the row's visual weight. -->
					<Button
						variant="ghost"
						size="sm"
						class="text-brand min-h-11 shrink-0"
						disabled={inert || completing === t.uid}
						onclick={() => oncompletetask?.(t.uid)}
					>
						<CheckIcon class="size-3.5" aria-hidden="true" />
						{completing === t.uid ? 'Marking…' : 'Done'}
					</Button>
				</div>
			{/each}
		</div>
	</div>
{:else if !hideWhenEmpty}
	<!-- The banner's all-clear: the same band geometry, brand tint traded for a quiet
	     hairline, so a page composed around the banner doesn't reflow when the list empties. -->
	<div
		class="text-muted-foreground flex h-full min-h-11 items-center gap-2 rounded-xl border px-4 py-3 text-sm"
	>
		<CheckIcon class="text-positive size-4 shrink-0" aria-hidden="true" />
		Nothing needs your attention
	</div>
{/if}
