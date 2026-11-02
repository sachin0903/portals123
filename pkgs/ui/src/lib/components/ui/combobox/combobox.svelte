<script lang="ts">
	// Searchable single-select: a text input with an inline suggestion list.
	//
	// INLINE, NOT A POPOVER — and that is the whole point. The first version of this wrapped a
	// Command list in a Popover (the way MultiSelect does) and was completely dead inside a Sheet:
	// Popover.Content portals to `document.body`, the Sheet is a modal with a focus trap, so the
	// panel rendered outside the trap and never received a click. MultiSelect gets away with it
	// because its only caller is a plain admin page; this one lives in the transfer drawer.
	//
	// So it follows `AddressLookup` instead — the same shape, the same roles, the same absolutely
	// positioned `role="listbox"` inside the field's own container. Nothing portals, so it works
	// in a sheet, a dialog or a page without knowing which it is in.
	//
	// The input IS the trigger: you type the name you want rather than opening a panel to type in
	// a second box. That also makes `allowCustom` natural — a register that will never be complete
	// should not block a firm from naming a ceding scheme it holds no id for.
	import CheckIcon from '@lucide/svelte/icons/check';
	import ChevronsUpDownIcon from '@lucide/svelte/icons/chevrons-up-down';
	import { cn } from '../../../utils.js';
	import { FIELD_CLASS } from '../../../field-shell.js';
	import type { ComboboxOption } from './types.js';

	let {
		options = [],
		value = $bindable(''),
		placeholder = 'Search…',
		emptyText = 'No matches.',
		/** Accept a typed value that is not in `options`. */
		allowCustom = false,
		/** The note under the field once the value is typed rather than picked.
		 *
		 *  It used to be one hardcoded sentence about a counterparty register — true of the transfer
		 *  drawer this control was built for, and wrong in the mouth of any other caller. The default
		 *  keeps that wording, so nothing already written changes; an empty string hides the note. */
		customNote = 'Not in the register — the name is recorded, but no counterparty id is.',
		disabled = false,
		invalid = false,
		id,
		name,
		class: className,
		'aria-label': ariaLabel,
		onOpen
	}: {
		options?: ComboboxOption[];
		value?: string;
		placeholder?: string;
		emptyText?: string;
		allowCustom?: boolean;
		customNote?: string;
		disabled?: boolean;
		invalid?: boolean;
		id?: string;
		name?: string;
		class?: string;
		'aria-label'?: string;
		/** Fired the first time this box opens — lazy pickers load their options here. */
		onOpen?: () => void;
	} = $props();

	let open = $state(false);
	let activeIndex = $state(-1);
	let focused = $state(false);

	const byValue = $derived(new Map(options.map((o) => [o.value, o])));
	const isCustom = $derived(!!value && !byValue.has(value));

	/** The current selection AS A PERSON READS IT.
	 *
	 *  A value and its label are not the same string whenever the options are keyed by an id — a
	 *  client picker, a transfer provider, a model — and the box must show the label. It showed the
	 *  raw `value`, so a picker with `value="22"` displayed "22": `choose()` put the label in the
	 *  box and then the re-sync effect below immediately overwrote it with the value again, which
	 *  is what happens on every mouse pick (the click blurs the field first) and on every
	 *  server-rendered pre-filled form.
	 *
	 *  Falls back to the value itself, which is what `allowCustom` needs: a typed value that is no
	 *  option IS its own label. */
	const display = $derived(byValue.get(value)?.label ?? value);

	/** The TYPING BUFFER — authoritative only while the field has focus.
	 *
	 *  The box used to hold one piece of state re-synced from `value` by an effect, which is why an
	 *  id-keyed picker displayed its id: the sync had no way to reach the label, and it ran after
	 *  every pick (a mouse click blurs the field first). Splitting the two removes the race
	 *  entirely — what is displayed is derived, so there is nothing to overwrite, and it is right
	 *  in server-rendered HTML too, where no effect runs. */
	let text = $state('');
	/** Whether the buffer holds something the user TYPED. `allowCustom` commits free text on the
	 *  way out, and the buffer is seeded with the current selection's label on focus — so without
	 *  this, tabbing through an id-keyed picker committed the label as the value: a mapping row
	 *  reading `dob` came back as `Date of birth · dob`. Only real typing may commit. */
	let dirty = $state(false);
	/** What the input actually shows: what is being typed, or the current selection. */
	const shown = $derived(focused ? text : display);

	const needle = $derived(text.trim().toLowerCase());
	/** Everything while the box still reads as the current selection (so opening shows the full
	 *  list), narrowed once the user actually types something different. */
	const matches = $derived(
		!focused || !needle || text === display
			? options
			: options.filter(
					(o) =>
						o.label.toLowerCase().includes(needle) || (o.hint ?? '').toLowerCase().includes(needle)
				)
	);
	const typed = $derived(text.trim());
	const exact = $derived(options.some((o) => o.label.toLowerCase() === typed.toLowerCase()));
	/** The "use what I typed" row, offered only when it is not already an option. */
	const offerCustom = $derived(allowCustom && !!typed && !exact);

	/** The rows, custom entry first — it is what the user just typed, so it is the likeliest pick. */
	const rows = $derived<{ value: string; label: string; hint?: string; custom?: boolean }[]>([
		...(offerCustom ? [{ value: typed, label: `Use “${typed}”`, custom: true }] : []),
		...matches
	]);

	function choose(next: string) {
		value = next;
		text = byValue.get(next)?.label ?? next;
		// A pick is not typing: leaving the field afterwards must not re-commit the label it put
		// in the box as if the user had written it.
		dirty = false;
		open = false;
		activeIndex = -1;
	}

	function keydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			open = true;
			if (rows.length === 0) return;
			const step = event.key === 'ArrowDown' ? 1 : -1;
			activeIndex = (activeIndex + step + rows.length) % rows.length;
			return;
		}
		if (event.key === 'Enter') {
			// Enter with nothing highlighted still commits what was typed, so a keyboard user
			// never has to arrow down to accept their own text.
			const pick = rows[activeIndex] ?? (offerCustom ? rows[0] : undefined);
			if (pick) {
				event.preventDefault();
				choose(pick.value);
			}
			return;
		}
		if (event.key === 'Escape' && open) {
			event.preventDefault();
			open = false;
			activeIndex = -1;
		}
	}

	function blur() {
		focused = false;
		// Commit free text on the way out: someone who types a name and tabs on has chosen it, and
		// silently discarding it would lose the entry with no message. Only what was TYPED, and
		// only when it is neither the value nor the label already showing — otherwise merely
		// visiting the field rewrites the selection. Nothing to reset either way: the box goes
		// back to showing the selection on its own, because that is derived.
		if (allowCustom && dirty && typed && typed !== value && typed !== display) value = typed;
		dirty = false;
		open = false;
		activeIndex = -1;
	}
</script>

<div class={cn('relative', open && 'z-50', className)}>
	<!-- The shared field shell (field-shell.ts), not a hand-written copy of it. This control used to
	     carry its own `h-9 rounded-md bg-background focus-visible:ring-2` — a taller, squarer,
	     lighter box than every Input beside it, which is what showed up as a different background
	     colour in the transfer drawer. `pr-9` is the only genuine difference: it leaves room for the
	     chevron. -->
	<input
		{id}
		{disabled}
		aria-invalid={invalid || undefined}
		class={cn(FIELD_CLASS, 'placeholder:text-muted-foreground pr-9')}
		value={shown}
		type="text"
		role="combobox"
		aria-expanded={open}
		aria-controls={id ? `${id}-options` : undefined}
		aria-autocomplete="list"
		autocomplete="off"
		aria-label={ariaLabel}
		{placeholder}
		oninput={(event) => {
			text = (event.currentTarget as HTMLInputElement).value;
			dirty = true;
			open = true;
			activeIndex = -1;
		}}
		onfocus={() => {
			// Start typing from the current selection, so the list opens showing everything and a
			// correction is an edit of the name rather than of an id. Not dirty yet — seeding is
			// not typing.
			text = display;
			dirty = false;
			focused = true;
			if (!open) onOpen?.();
			open = true;
		}}
		onblur={blur}
		onkeydown={keydown}
	/>
	<ChevronsUpDownIcon
		class="text-muted-foreground pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 opacity-50"
		aria-hidden="true"
	/>

	{#if open && !disabled}
		<ul
			id={id ? `${id}-options` : undefined}
			role="listbox"
			class="bg-popover absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-md border shadow-md"
		>
			{#if rows.length === 0}
				<li class="text-muted-foreground px-3 py-2 text-sm">{emptyText}</li>
			{:else}
				<!-- UNKEYED, deliberately. Keying on `row.value` threw `each_key_duplicate` the moment
				     a register held two providers with the same name ("Carey Pensions UK" twice), which
				     killed the whole component — the reason it looked dead on click. These rows hold no
				     state, so a key buys nothing and can only reintroduce that crash on data the
				     component does not control. -->
				{#each rows as row, index}
					<li role="option" aria-selected={index === activeIndex}>
						<!-- mousedown, not click: blur fires first on click and would close the list
						     before the selection landed — the same trap the Popover version fell into. -->
						<button
							type="button"
							tabindex="-1"
							onmousedown={(event) => {
								event.preventDefault();
								choose(row.value);
							}}
							onmouseenter={() => (activeIndex = index)}
							class={cn(
								'flex w-full items-center gap-2 px-3 py-2 text-left text-sm',
								index === activeIndex ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/50'
							)}
						>
							<CheckIcon
								class={cn('size-4 shrink-0', value === row.value ? 'opacity-100' : 'opacity-0')}
								aria-hidden="true"
							/>
							<span class="min-w-0 flex-1">
								<span class={cn('block truncate', row.custom && 'italic')}>{row.label}</span>
								{#if row.hint}
									<span class="text-muted-foreground block truncate font-mono text-xs">
										{row.hint}
									</span>
								{/if}
							</span>
						</button>
					</li>
				{/each}
			{/if}
		</ul>
	{/if}
</div>

{#if isCustom && customNote}
	<p class="text-muted-foreground mt-1 text-xs">{customNote}</p>
{/if}

<!-- Emitted so the field works inside a plain form POST, as MultiSelect does. -->
{#if name}
	<input type="hidden" {name} {value} />
{/if}
