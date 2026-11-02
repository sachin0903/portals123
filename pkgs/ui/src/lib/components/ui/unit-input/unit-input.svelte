<script lang="ts">
	// A length field. ONE control, not an Input beside a SelectMenu: the unit belongs to the number, so it sits INSIDE the box (the `MoneyInput` arrangement), and it is a MENU ONLY WHEN THERE IS A CHOICE — `create.ts` declares single-unit properties, and a one-option dropdown is a chevron promising nothing.
	// NOT type="number", whose scroll wheel silently edits a focused padding field; `inputmode="decimal"` keeps the mobile keypad. In and out is ONE CSS string, taken apart by `dimension.ts`.
	import type { Snippet } from 'svelte';
	import * as DropdownMenu from '../dropdown-menu/index.js';
	import { cn } from '../../../utils.js';
	import { FIELD_WRAPPER_CLASS } from '../../../field-shell.js';
	import { defaultUnit, joinDimension, splitDimension } from './dimension.js';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';

	let {
		value = $bindable(''),
		units = [],
		id,
		name,
		placeholder = 'auto',
		disabled = false,
		min,
		class: className,
		adornment,
		onchange
	}: {
		value?: string;
		/** The units this property may take. One ⇒ shown as text; none ⇒ a plain number field. */
		units?: readonly string[];
		id?: string;
		name?: string;
		placeholder?: string;
		disabled?: boolean;
		/** Clamped on commit, so a negative padding cannot be typed where none is allowed. */
		min?: number;
		class?: string;
		/** Rendered INSIDE the box, before the number — the side glyph on a `BoxSides` field. An icon
		 *  beside the box instead would cost the field the width it needs to show its own number. */
		adornment?: Snippet;
		onchange?: (value: string) => void;
	} = $props();

	const parts = $derived(splitDimension(value, units));
	// The unit for a value carrying none (a blank field, a unitless `0`), held in state so the author's choice survives clearing the number.
	let fallbackUnit = $state<string | null>(null);
	const unit = $derived(parts.unit ?? fallbackUnit ?? defaultUnit(units));
	/** An EMPTY field whose placeholder is a CSS keyword shows no unit: there is no number for one to
	 *  qualify, and `medium px` beside `normal px` reads as a mistake rather than as a default. Typing
	 *  a digit reveals it. Tested on the first character rather than by pattern — `auto`, `medium` and
	 *  `normal` are the placeholders in play and none of them starts like a number. */
	const keywordPlaceholder = $derived(
		placeholder !== '' && !'0123456789.-'.includes(placeholder[0] ?? '')
	);
	const hideUnit = $derived(parts.value === '' && keywordPlaceholder);

	function commit(next: string): void {
		value = next;
		onchange?.(next);
	}

	function setNumber(text: string): void {
		// `unit` is passed unconditionally: `joinDimension` refuses to append one to a keyword or a cleared field, so there is no branch here to get wrong.
		commit(joinDimension(text, unit));
	}

	function setUnit(next: string): void {
		fallbackUnit = next;
		commit(joinDimension(parts.value, next));
	}

	function clampOnBlur(text: string): void {
		if (min === undefined || parts.keyword) return;
		const n = Number(text);
		if (Number.isFinite(n) && n < min) setNumber(String(min));
	}
</script>

<div
	class={cn(
		FIELD_WRAPPER_CLASS,
		'flex items-center gap-1',
		disabled && 'bg-input/50 pointer-events-none opacity-50',
		className
	)}
>
	{#if adornment}
		<span class="text-muted-foreground flex shrink-0 items-center" aria-hidden="true">
			{@render adornment()}
		</span>
	{/if}
	<input
		{id}
		{disabled}
		{placeholder}
		type="text"
		inputmode="decimal"
		autocomplete="off"
		spellcheck="false"
		value={parts.value}
		class="placeholder:text-muted-foreground w-full min-w-0 flex-1 bg-transparent text-sm tabular-nums outline-none"
		oninput={(e) => setNumber(e.currentTarget.value)}
		onblur={(e) => clampOnBlur(e.currentTarget.value)}
	/>

	{#if hideUnit}
		<!-- The placeholder IS the answer while the field is empty (see `hideUnit`). -->
	{:else if parts.keyword}
		<!-- `auto`, `inherit`, a calc(), a token: no unit to choose, and offering one invites an edit `joinDimension` would only refuse. -->
	{:else if units.length > 1}
		<DropdownMenu.Root>
			<DropdownMenu.Trigger
				{disabled}
				aria-label="Unit"
				class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 -mr-1 flex h-6 shrink-0 items-center gap-0.5 rounded px-1 text-[0.7rem] font-semibold transition-colors focus-visible:ring-2 focus-visible:outline-none"
			>
				{unit}
				<ChevronDownIcon class="size-3" />
			</DropdownMenu.Trigger>
			<DropdownMenu.Content align="end" class="min-w-16">
				{#each units as u (u)}
					<DropdownMenu.Item onclick={() => setUnit(u)}>{u}</DropdownMenu.Item>
				{/each}
			</DropdownMenu.Content>
		</DropdownMenu.Root>
	{:else if unit}
		<span class="text-muted-foreground shrink-0 text-[0.7rem] font-semibold select-none">
			{unit}
		</span>
	{/if}
</div>
{#if name}
	<input type="hidden" {name} value={value ?? ''} />
{/if}
