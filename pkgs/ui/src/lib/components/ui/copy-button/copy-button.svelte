<script lang="ts">
	// Copy a short machine value — an id, a key, a correlation reference — to the clipboard, with the
	// tick that confirms it happened.
	//
	// This exists because the same twelve lines had been written four times (the event detail's
	// correlation id, the account security key, the storage credentials, and now a document's
	// reference) and had already drifted: two report success with a transient tick, one with a toast,
	// and only one of them handles the clipboard being unavailable. A copy affordance is not the place
	// for three behaviours.
	//
	// TWO THINGS IT MUST KEEP DOING. The clipboard is not always there — it needs a secure context and
	// a user gesture, so it is simply absent over plain http and can be refused by permission policy —
	// and a rejected write must therefore leave the value SELECTABLE rather than throw: nothing about a
	// failed copy should break the page it sits on, and an unhandled rejection here would. And the
	// button carries an `aria-label` naming WHAT is copied, because "Copy" repeated four times down a
	// definition list tells a screen-reader user nothing about which one they are on.
	//
	// Pass `children` to make the VALUE ITSELF the click target rather than a separate icon beside it
	// — an id is a small target and the obvious thing to click is the thing you want. The content is
	// caller-supplied on purpose: this component still never renders `value`, so the rule that a
	// secret is not duplicated into this component's own markup survives the addition.
	import type { Snippet } from 'svelte';
	import CheckIcon from '@lucide/svelte/icons/check';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import { Button } from '../button/index.js';
	import { cn } from '../../../utils.js';

	let {
		value,
		label = 'Copy',
		size = 'sm',
		reveal = 'always',
		class: className,
		oncopy,
		children
	}: {
		/** The text put on the clipboard. */
		value: string;
		/** Names the thing being copied, for the accessible label and the title: "Copy document id". */
		label?: string;
		/** `sm` for a row-inline affordance (the default), `md` where it stands alone. */
		size?: 'sm' | 'md';
		/**
		 * `hover` keeps the glyph out of the way until the control is the target — a contact fact
		 * on a text row, where a permanent copy icon reads as extra spacing between the values.
		 * Keyboard focus still reveals it (`group-focus-visible` on the Button's `group/button`).
		 */
		reveal?: 'always' | 'hover';
		class?: string;
		/** Fired only on a SUCCESSFUL write — for a caller that wants its own toast as well. */
		oncopy?: () => void;
		/**
		 * Visible content INSIDE the button, so the whole thing is one click target (an id, a
		 * reference). Omit for the icon-only affordance.
		 */
		children?: Snippet;
	} = $props();

	let copied = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function copy() {
		try {
			await navigator.clipboard.writeText(value);
			copied = true;
			clearTimeout(timer);
			// Long enough to be read, short enough that the button is ready for the next value in a list.
			timer = setTimeout(() => (copied = false), 1500);
			oncopy?.();
		} catch {
			// No clipboard (insecure context, refused permission) — the value is still selectable text,
			// so the quiet no-op is the correct outcome. Never rethrow: this is a decoration on someone
			// else's page.
		}
	}

	$effect(() => () => clearTimeout(timer));

	const glyph = $derived(size === 'sm' ? 'size-3.5' : 'size-4');
	// `invisible`, not `hidden`: the glyph must keep its box in the layout at all times, or revealing
	// it on hover/focus adds width to the button and shifts whatever sits after it in the row (the
	// profile hero's copyable chips wrap in a `flex-wrap` row, so a `hidden` glyph reappearing pushed
	// every later chip sideways).
	const hoverGlyph = $derived(
		reveal === 'hover' && 'invisible group-hover/button:visible group-focus-visible/button:visible'
	);
</script>

<Button
	variant="ghost"
	size="sm"
	class={cn(
		'cursor-pointer',
		children
			? // Sized by its content, not a square: it wraps a value and has to sit on a text row
				// without changing that row's height. `min-w-0` so a long value truncates or wraps by
				// the caller's rule instead of forcing the row wider. `whitespace-normal` overrides
				// the Button's nowrap so an email can wrap inside its own item.
				'h-auto min-w-0 max-w-full shrink justify-start gap-1.5 px-1.5 py-0.5 text-left font-normal whitespace-normal'
			: cn(size === 'sm' ? 'size-6 p-0' : 'size-8 p-0', 'shrink-0'),
		className
	)}
	aria-label={copied ? `${label} — copied` : label}
	title={label}
	onclick={copy}
>
	{#if children}{@render children()}{/if}
	{#if copied}
		<CheckIcon class={cn(glyph, 'shrink-0 text-success')} />
	{:else}
		<CopyIcon class={cn(glyph, 'shrink-0', hoverGlyph)} />
	{/if}
</Button>
