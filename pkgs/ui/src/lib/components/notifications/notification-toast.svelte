<script lang="ts">
	// The pop-up card for a notification that has just arrived — the Slack/Outlook affordance: it
	// appears top-right, goes away on its own, can be dismissed with the X, and clicking it takes you
	// to the thing it is about.
	//
	// Rendered INSIDE a svelte-sonner toast (`toast.custom`), which owns the stacking, the timer, the
	// pause-on-hover and the enter/exit motion — the notification centre should not grow a second
	// implementation of a toast stack when the design system already has one. Two consequences of
	// that arrangement, both verified against svelte-sonner 1.1.1 rather than assumed:
	//  - a toast carrying a `component` is NOT given sonner's own card styling (`data-styled` is
	//    false for it), so the whole surface below is ours and must state its own background, border
	//    and shadow — it inherits nothing;
	//  - sonner suppresses its own close button for a `component` toast and instead injects a
	//    `closeToast` prop, which is what the X calls.
	//
	// Deliberately NOT the same component as `notification-item.svelte`: a row sits in a list with a
	// timestamp, a read/unread weight, a mark-read tick and a dismiss that HIDES the notification
	// from the bell. A pop-up has none of those jobs — its X closes the CARD and leaves the
	// notification unread in the bell, which is the behaviour every one of these has and the
	// opposite of the row's X.
	//
	// That difference is the reason the card says "Close" and the row says "Dismiss". Closing a
	// pop-up is not a claim about the notification: it is still unread, still on the badge, still
	// in the bell. Only the row's X is a decision about the notification itself.
	import XIcon from '@lucide/svelte/icons/x';
	import { cn } from '../../utils.js';
	import { categoryMeta, toneClasses, type NotificationItem } from './describe.js';

	let {
		item,
		onMarkRead,
		closeToast,
		onCloseAll,
		class: className
	}: {
		item: NotificationItem;
		/** Clicking through reads it, the same as clicking the row. */
		onMarkRead?: (uid: string) => void;
		/** Injected by svelte-sonner into every `component` toast — closes THIS card. */
		closeToast?: () => void;
		/**
		 * Closes the WHOLE stack. Passed only to the card at the front of a batch, because a
		 * "Close all" on a lone card does exactly what its own X does and reads as two buttons
		 * for one job. Like the X it is display-only: it shuts the cards and makes no claim that
		 * anything was read.
		 */
		onCloseAll?: () => void;
		class?: string;
	} = $props();

	const meta = $derived(categoryMeta(item.category));
	const Icon = $derived(meta.icon);

	function handleClick() {
		onMarkRead?.(item.uid);
		closeToast?.();
		// Navigation is the browser's: the anchor's href does it. `pkgs/ui` carries no `$app/*`
		// import so it stays mountable in all three portals — the rule `notification-item.svelte`
		// already follows.
	}

	// The X sits INSIDE the card's <a>, so it must not be a nested <button> (invalid HTML) and must
	// swallow the click, or dismissing the pop-up would navigate away — the one thing a person
	// reaching for an X is trying not to do.
	function handleDismiss(event: Event) {
		event.preventDefault();
		event.stopPropagation();
		closeToast?.();
	}

	/** Same swallow-the-click rule as the X: this sits inside the card's anchor. */
	function handleCloseAll(event: Event) {
		event.preventDefault();
		event.stopPropagation();
		onCloseAll?.();
	}

	// Hovering BRIGHTENS it. The first cut used `hover:bg-muted/40`, which is a grey over a popover
	// surface — so pointing at the card to read it made it duller, the opposite of what a hover
	// state is for on something you are being invited to click. The highlight is the branded ring
	// plus a deeper shadow (it lifts toward you) rather than a background wash, so the text keeps
	// its full contrast the whole time.
	const card =
		'bg-popover text-popover-foreground ring-foreground/10 flex w-full items-start gap-3 rounded-lg p-3 text-left shadow-lg ring-1 transition-shadow';
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
		<span class="text-muted-foreground/80 block text-[0.7rem]">{meta.label}</span>
		<span class="mt-0.5 line-clamp-2 block text-sm font-semibold">{item.title}</span>
		{#if item.body}
			<span class="text-muted-foreground mt-0.5 line-clamp-2 block text-xs">{item.body}</span>
		{/if}
	</span>
	<span class="-mt-0.5 -mr-1 flex shrink-0 items-start gap-0.5">
		{#if onCloseAll}
			<!-- Text, not a second icon: two X-ish glyphs side by side give no clue which one is the
			     bulk action, and the whole point of this control is that it does MORE than the X
			     beside it. -->
			<span
				role="button"
				tabindex="0"
				aria-label="Close all notifications"
				title="Close all"
				onclick={handleCloseAll}
				onkeydown={(e) => {
					if (e.key === 'Enter' || e.key === ' ') handleCloseAll(e);
				}}
				class="text-muted-foreground/70 hover:text-foreground hover:bg-muted-foreground/10 flex h-6 shrink-0 items-center rounded px-1.5 text-[0.7rem] font-medium transition-colors"
			>
				Close all
			</span>
		{/if}
		<span
			role="button"
			tabindex="0"
			aria-label="Close"
			title="Close"
			onclick={handleDismiss}
			onkeydown={(e) => {
				if (e.key === 'Enter' || e.key === ' ') handleDismiss(e);
			}}
			class="text-muted-foreground/60 hover:text-foreground hover:bg-muted-foreground/10 flex size-6 shrink-0 items-center justify-center rounded transition-colors"
		>
			<XIcon class="size-3.5" />
		</span>
	</span>
{/snippet}

{#if item.link}
	<a
		data-notification-toast
		href={item.link}
		onclick={handleClick}
		class={cn(card, 'hover:ring-primary/40 hover:shadow-xl', className)}
	>
		{@render inner()}
	</a>
{:else}
	<!-- No link means there is nowhere to send anyone, so the card is not presented as clickable —
	     a pop-up that looks actionable and does nothing is worse than a plain one. -->
	<div data-notification-toast class={cn(card, className)}>
		{@render inner()}
	</div>
{/if}
