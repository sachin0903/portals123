<script lang="ts">
	// One notification row — shared by the bell dropdown and the full feed. Renders as an <a> when
	// the notification carries a link (native navigation, so this component needs no $app/navigation
	// and stays portable across the three portals) and a plain block otherwise. Clicking marks it
	// read via the injected callback; unread rows are weighted bold and carry an accent bar down
	// their left edge.
	import XIcon from '@lucide/svelte/icons/x';
	import CheckIcon from '@lucide/svelte/icons/check';
	import { cn } from '../../utils.js';
	import { categoryMeta, toneClasses, relativeTime, type NotificationItem } from './describe.js';
	import { now } from './now.svelte.js';

	let {
		item,
		onMarkRead,
		onActivate,
		onDismiss,
		dayGrouped = false,
		boxed = false,
		class: className
	}: {
		item: NotificationItem;
		/** Fire-and-forget: the app POSTs to its own mark-read proxy. */
		onMarkRead?: (uid: string) => void;
		/** Fired on EVERY click (read or not) — the bell uses it to close its popover on navigation. */
		onActivate?: () => void;
		/** Remove this notification from the feed. Shown as a hover X when provided. */
		onDismiss?: (uid: string) => void;
		/**
		 * The row sits under a day heading, so the stamp is a TIME rather than an age.
		 *
		 * "5d ago" beneath a heading that already says "Sunday, 6 September" is the same fact
		 * twice, and the less precise of the two. The bell has no headings, so it leaves this off
		 * and keeps the relative age, which is the right read for a list of recent arrivals.
		 */
		dayGrouped?: boolean;
		/**
		 * Draw the row as its own card — a border, the card ground, and a gap to its neighbours.
		 *
		 * For the archive, where rows ran edge to edge into one undifferentiated column and the
		 * only thing between two notifications was a line of whitespace. The bell's dropdown keeps
		 * the flush list: it is a narrow panel of recent arrivals, and boxing eight rows in it
		 * spends most of the width on borders.
		 *
		 * Deliberately NOT folded into `dayGrouped`. The same caller happens to set both, but one
		 * decides what the stamp says and the other how the row is drawn — a single flag meaning
		 * "the archive" would need untangling the first time either is wanted on its own.
		 */
		boxed?: boolean;
		class?: string;
	} = $props();

	const meta = $derived(categoryMeta(item.category));
	const unread = $derived(item.read_at === null);
	const Icon = $derived(meta.icon);
	// Reads the shared ticking clock so the relative age re-computes live (no tab-switch/refresh).
	const age = $derived(relativeTime(item.created_at, now()));
	// The relative phrase is the readable answer; the exact stamp is one hover away. "3d ago" is the
	// right thing to show and the wrong thing to have to act on, and a row is the only place the
	// precise time exists at all — nothing else on the notification surfaces carries it.
	const exact = $derived.by(() => {
		const at = new Date(item.created_at);
		return Number.isNaN(at.getTime()) ? undefined : at.toLocaleString('en-GB');
	});

	/** A clock time under a day heading, the relative age otherwise — see `dayGrouped`. Falls back
	 *  to the age if the stamp will not parse, rather than rendering "Invalid Date" in the corner. */
	const stamp = $derived.by(() => {
		if (!dayGrouped) return age;
		const at = new Date(item.created_at);
		if (Number.isNaN(at.getTime())) return age;
		return at.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
	});

	function handleClick() {
		if (unread) onMarkRead?.(item.uid);
		onActivate?.();
	}

	// The X sits INSIDE the row's <a>/<button>, so it must not be a nested <button> (invalid HTML)
	// and must swallow the click so the row does not navigate/mark-read as it dismisses.
	function handleDismiss(event: Event) {
		event.preventDefault();
		event.stopPropagation();
		onDismiss?.(item.uid);
	}

	// Mark this one read WITHOUT navigating (the row click both reads AND follows the link; this is
	// the explicit "just mark it read" affordance). Same swallow-the-click rule as dismiss.
	function handleMarkRead(event: Event) {
		event.preventDefault();
		event.stopPropagation();
		onMarkRead?.(item.uid);
	}
</script>

{#snippet inner()}
	<span
		class={cn(
			'mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full',
			toneClasses(meta.tone)
		)}
	>
		<Icon class="size-4" />
	</span>
	<span class="min-w-0 flex-1">
		<!-- Headline and stamp on one line, the stamp pushed right. The old layout put
		     "Task · 28 Aug" on a third line under the body, which read as part of the content and
		     buried the one thing a reader scans a feed by. -->
		<span class="flex items-baseline gap-2">
			<!-- `line-clamp-2`, not `truncate`: a single clamped line turned every headline in a
			     narrow column into "New message fro…", which names nobody. Two lines carry the
			     sender or the subject, which is the whole point of the headline. -->
			<span class={cn('line-clamp-2 text-sm', unread ? 'font-semibold' : 'font-medium')}
				>{item.title}</span
			>
			{#if unread}
				<!-- Unread is the BAR down the left edge, not a dot here. A dot between the headline
				     and the stamp stole width from the one line that has to be readable, and on a
				     narrow column that is what pushed titles into an ellipsis. Nothing visible is
				     left in the flow; the state is announced instead. -->
				<span class="sr-only">Unread</span>
			{/if}
			<span
				class="text-muted-foreground/80 ml-auto shrink-0 text-[0.7rem] tabular-nums"
				title={exact}
			>
				{stamp}
			</span>
		</span>
		<span class="text-muted-foreground/80 mt-0.5 block text-[0.7rem]">{meta.label}</span>
		{#if item.body}
			<span class="text-muted-foreground mt-1 line-clamp-2 block text-xs">{item.body}</span>
		{/if}
	</span>
	{#if (unread && onMarkRead) || onDismiss}
		<!-- Pointer-only affordances: revealed on hover, 24px, and `pointer-coarse:hidden` because a
		     thumb has no hover and the row itself is the tap. Dismiss-all and the feed page carry
		     the same actions at full size. -->
		<span class="-mr-1 -mt-0.5 flex shrink-0 items-center gap-0.5">
			{#if unread && onMarkRead}
				<span
					role="button"
					tabindex="-1"
					aria-label="Mark as read"
					title="Mark as read"
					onclick={handleMarkRead}
					onkeydown={(e) => {
						if (e.key === 'Enter' || e.key === ' ') handleMarkRead(e);
					}}
					class="text-muted-foreground/50 hover:text-foreground hover:bg-muted-foreground/10 pointer-coarse:hidden flex size-6 items-center justify-center rounded opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
				>
					<CheckIcon class="size-3.5" />
				</span>
			{/if}
			{#if onDismiss}
				<span
					role="button"
					tabindex="-1"
					aria-label="Dismiss notification"
					title="Dismiss"
					onclick={handleDismiss}
					onkeydown={(e) => {
						if (e.key === 'Enter' || e.key === ' ') handleDismiss(e);
					}}
					class="text-muted-foreground/50 hover:text-foreground hover:bg-muted-foreground/10 pointer-coarse:hidden flex size-6 items-center justify-center rounded opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
				>
					<XIcon class="size-3.5" />
				</span>
			{/if}
		</span>
	{/if}
{/snippet}

{#if item.link}
	<a
		href={item.link}
		onclick={handleClick}
		class={cn(
			'group flex gap-3 transition-colors',
			boxed
				? // A card of its own: the ground and the border make the row an object rather than a
					// band of text, which is what lets a reader's eye count them.
					'bg-card hover:bg-muted/40 border-border/70 rounded-lg border px-4 py-3 shadow-sm'
				: 'hover:bg-muted/60 rounded-md px-3 py-2.5',
			// Unread is the BAR and the weight, and nothing else. A background wash was tried both
			// ways round and neither worked: strong enough to notice, it turned every unread row
			// grey and made the list look disabled rather than urgent; faint enough not to, it was
			// invisible. The bar is found in one sweep of the eye, the card stays white, and the
			// headline keeps its full width.
			unread &&
				(boxed
					? 'border-l-primary border-l-[3px] pl-[calc(1rem-2px)]'
					: 'border-l-primary border-l-[3px] pl-[calc(0.75rem-3px)]'),
			className
		)}
	>
		{@render inner()}
	</a>
{:else}
	<button
		type="button"
		onclick={handleClick}
		class={cn(
			'group flex w-full gap-3 text-left transition-colors',
			boxed
				? 'bg-card hover:bg-muted/40 border-border/70 rounded-lg border px-4 py-3 shadow-sm'
				: 'hover:bg-muted/60 rounded-md px-3 py-2.5',
			unread &&
				(boxed
					? 'border-l-primary border-l-[3px] pl-[calc(1rem-2px)]'
					: 'border-l-primary border-l-[3px] pl-[calc(0.75rem-3px)]'),
			className
		)}
	>
		{@render inner()}
	</button>
{/if}
