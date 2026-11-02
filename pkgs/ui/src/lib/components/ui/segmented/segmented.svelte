<script lang="ts" module>
	import type { Component } from 'svelte';

	export interface SegmentedOption {
		value: string;
		label: string;
		/** An icon instead of (or beside) the label — for a toggle whose choices are visual, like
		 *  text alignment, where three words take more room than the control they sit in and read
		 *  worse than the pictures of what they do. */
		icon?: Component<{ class?: string }>;
		/** The accessible name when `icon` is given without a visible label. Falls back to `label`. */
		ariaLabel?: string;
		disabled?: boolean;
	}
</script>

<script lang="ts">
	import { cn } from '../../../utils.js';

	/**
	 * THE mode/view toggle (docs/design-language.md). It exists because 22+ hand-rolled `bg-muted`
	 * pill groups did, and it must keep absorbing that need rather than growing a rival: an
	 * icon-only variant belongs here, not in a second component.
	 */
	let {
		options,
		value = $bindable(),
		disabled = false,
		size = 'default',
		shape = 'pill',
		iconOnly = false,
		label,
		onValueChange,
		class: className
	}: {
		/** `readonly` so an `as const` option list can be passed straight in — callers usually
		 *  want the literal union type derived from it, and this component only iterates. */
		options: readonly SegmentedOption[];
		/** Optional so a caller can start with nothing selected: some choices should be made
		 *  deliberately rather than defaulted. Renders with no active segment until set. */
		value?: string;
		disabled?: boolean;
		/** `sm` for a control inside a dense inspector row rather than a page band; `lg` where the
		 *  segments are tapped — a settings page on a phone — so each is a 44px target in itself
		 *  (the group scrolls horizontally, which clips an invisible extension). */
		size?: 'sm' | 'default' | 'lg';
		/** `square` for a toggle sitting in a row of rectangular fields, where a pill reads as a
		 *  different KIND of control from its neighbours. */
		shape?: 'pill' | 'square';
		/** Show icons alone, with the label as the accessible name. Ignored for options with no icon,
		 *  which always show their text — a blank segment would be unusable. */
		iconOnly?: boolean;
		/**
		 * What the GROUP is choosing between, as its accessible name.
		 *
		 * The segments have names; the tablist did not — so a screen reader read "Desktop, tab, 1 of 2"
		 * with no indication of what was being chosen. Worth naming for a second reason too: this control
		 * is a mode switch, and what the mode APPLIES TO is exactly the thing a label carries. The email
		 * editor's is "Preview width", which is the distinction between previewing a size and selecting a
		 * style target — and the absence of that word is what made the control read as the latter.
		 *
		 * Optional, because a group whose meaning is obvious from the segments and their surroundings does
		 * not need one and a redundant label is noise of its own.
		 */
		label?: string;
		onValueChange?: (value: string) => void;
		class?: string;
	} = $props();

	function select(v: string) {
		value = v;
		onValueChange?.(v);
	}
</script>

<div
	role="tablist"
	aria-label={label}
	class={cn(
		'bg-muted inline-flex max-w-full items-center overflow-x-auto',
		size === 'sm' ? 'h-7 gap-0.5 p-0.5' : size === 'lg' ? 'h-12 gap-1 p-0.5' : 'h-8 gap-1 p-0.5',
		shape === 'square' ? 'rounded-md' : 'rounded-full',
		className
	)}
>
	{#each options as option (option.value)}
		{@const Icon = option.icon}
		{@const showLabel = !iconOnly || !Icon}
		<button
			type="button"
			role="tab"
			disabled={disabled || option.disabled}
			aria-selected={value === option.value}
			aria-label={showLabel ? undefined : (option.ariaLabel ?? option.label)}
			title={showLabel ? undefined : (option.ariaLabel ?? option.label)}
			onclick={() => select(option.value)}
			class={cn(
				'inline-flex shrink-0 items-center justify-center gap-1.5 font-medium transition-colors disabled:opacity-50',
				size === 'sm' ? 'h-6 text-xs' : size === 'lg' ? 'h-11 text-sm' : 'h-7 text-sm',
				shape === 'square' ? 'rounded' : 'rounded-full',
				// An icon-only segment is square-ish; a labelled one is padded for its text.
				showLabel
					? size === 'sm'
						? 'px-2'
						: size === 'lg'
							? 'px-4'
							: 'px-3.5'
					: size === 'sm'
						? 'w-6'
						: size === 'lg'
							? 'w-11'
							: 'w-7',
				value === option.value
					? 'bg-background text-foreground shadow-sm'
					: 'text-muted-foreground hover:text-foreground'
			)}
		>
			{#if Icon}<Icon class={size === 'sm' ? 'size-3.5' : 'size-4'} />{/if}
			{#if showLabel}{option.label}{/if}
		</button>
	{/each}
</div>
