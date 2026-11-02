<script lang="ts">
	// Searchable multi-select: a popover over a Command list, with the chosen
	// values shown as removable chips on the trigger.
	//
	// Built on Command rather than Select because the lists it exists for are long
	// and unbrowsable — a firm's SEI DSR teams run to 400+ entries, so type-to-filter
	// is the only usable interaction. Command also gives keyboard nav for free.
	//
	// `name` makes it work inside a plain form POST: each selected value is emitted
	// as a hidden input, so a page using SvelteKit form actions needs no client JS
	// to submit. Without `name` it is bind:value only.
	import CheckIcon from '@lucide/svelte/icons/check';
	import ChevronsUpDownIcon from '@lucide/svelte/icons/chevrons-up-down';
	import XIcon from '@lucide/svelte/icons/x';
	import { Badge } from '../badge/index.js';
	import * as Command from '../command/index.js';
	import * as Popover from '../popover/index.js';
	import { cn } from '../../../utils.js';
	import { FIELD_CLASS } from '../../../field-shell.js';
	import type { MultiSelectOption } from './types.js';

	let {
		options = [],
		value = $bindable([]),
		/** Present ⇒ CONTROLLED: the next selection is emitted, not written back. The same two modes
		 *  `FilterSelect` and `ListingToolbar` carry, and for the same reason — a filter bar whose
		 *  selection lives in the URL decides its own next value and navigates. */
		onValueChange,
		placeholder = 'Select…',
		searchPlaceholder = 'Search…',
		emptyText = 'No matches.',
		/** Chips rendered before collapsing to "+N more". */
		maxChips = 3,
		/** Swap the two type treatments: the LABEL is set in mono and the hint in plain text. For a
		 *  list whose identifier is the thing being picked — the Events event-type filter picks raw
		 *  `event_type` values and shows the human sentence as the hint — where the default (plain
		 *  label, mono hint) is the right way round for a name with a code beside it. */
		monoLabel = false,
		disabled = false,
		/** Portal the panel to `document.body` (the default). Turn off inside a Dialog or Sheet:
		 *  a portalled panel sits outside that modal's focus trap and never receives a click. */
		portal = true,
		/** Render the list itself, with no trigger. A filter tag is already the dropdown; nesting
		 *  this control's own popover inside it was a second click for the same list. */
		inline = false,
		/** The type-to-narrow box. On for the long lists this control was built for; a filter with
		 *  a handful of values passes false and the list stands alone. */
		searchable = true,
		/** Hide the list until the query has text. Values already chosen stay visible so they
		 *  can be cleared without being searched for again. */
		autocomplete = false,
		/** One value, and picking it again clears it. The empty row (`emptyLabel`) is the other
		 *  way back to "no filter". */
		single = false,
		emptyLabel,
		id,
		name,
		/** `aria-invalid` on the trigger. The shell paints the destructive ring from that attribute. */
		invalid = false,
		class: className,
		'aria-label': ariaLabel,
		onOpenChange
	}: {
		options?: MultiSelectOption[];
		value?: string[];
		onValueChange?: (next: string[]) => void;
		placeholder?: string;
		searchPlaceholder?: string;
		emptyText?: string;
		maxChips?: number;
		monoLabel?: boolean;
		disabled?: boolean;
		portal?: boolean;
		inline?: boolean;
		searchable?: boolean;
		autocomplete?: boolean;
		single?: boolean;
		emptyLabel?: string;
		id?: string;
		name?: string;
		invalid?: boolean;
		class?: string;
		'aria-label'?: string;
		/** Fired when the panel opens or closes — lazy pickers load their options on open. */
		onOpenChange?: (open: boolean) => void;
	} = $props();

	let open = $state(false);

	const byValue = $derived(new Map(options.map((o) => [o.value, o])));
	const selected = $derived(value.map((v) => byValue.get(v) ?? { value: v, label: v }));
	const shown = $derived(selected.slice(0, maxChips));
	const overflow = $derived(selected.length - shown.length);

	/** The options as [group, options] pairs, ungrouped first, in the order given — see the `group`
	 *  doc on `MultiSelectOption`. Command hides a group whose items all filter out, so an empty
	 *  section never leaves a stray heading behind. */
	const sections = $derived.by(() => {
		const out: { group: string | null; items: MultiSelectOption[] }[] = [];
		for (const option of options) {
			const group = option.group ?? null;
			const last = out.at(-1);
			if (last && last.group === group) last.items.push(option);
			else out.push({ group, items: [option] });
		}
		return out;
	});

	/** One writer for both modes, so the controlled path cannot forget a branch. */
	const commit = (next: string[]) => {
		if (onValueChange) onValueChange(next);
		else value = next;
	};
	const toggle = (option: MultiSelectOption) => {
		if (option.disabled) return;
		const on = value.includes(option.value);
		if (single) {
			commit(on ? [] : [option.value]);
			return;
		}
		commit(on ? value.filter((v) => v !== option.value) : [...value, option.value]);
	};
	const remove = (target: string) => {
		commit(value.filter((v) => v !== target));
	};

	const picked = (optionValue: string) => value.includes(optionValue);

	let commandQuery = $state('');
	const showSearch = $derived(searchable || autocomplete);
	/** An autocomplete keeps unselected options out of the list until there is a query. */
	const listed = $derived.by(() => {
		if (!autocomplete || commandQuery.trim()) return sections;
		return sections
			.map((section) => ({
				...section,
				items: section.items.filter((item) => value.includes(item.value))
			}))
			.filter((section) => section.items.length > 0);
	});
	const prompt = $derived(autocomplete && !commandQuery.trim() ? 'Type to search' : emptyText);
</script>

{#snippet optionsList()}
	<Command.Root>
		{#if showSearch}
			<Command.Input placeholder={searchPlaceholder} bind:value={commandQuery} />
		{/if}
		<Command.List>
			<Command.Empty>{prompt}</Command.Empty>
			{#if single && emptyLabel}
				<!-- The way back. A tick cannot be a radio: picking the chosen row again also clears,
				     and this row is the one that says what "cleared" means ("Any", "All roles"). -->
				<Command.Item
					value={emptyLabel}
					onSelect={() => commit([])}
					class="cursor-pointer [&>.cn-command-item-indicator]:hidden"
					data-state={value.length === 0 ? 'checked' : 'unchecked'}
				>
					<CheckIcon class={cn('size-4', value.length === 0 ? 'opacity-100' : 'opacity-0')} />
					<span
						class="text-muted-foreground text-sm"
						data-selected={value.length === 0 ? 'true' : undefined}>{emptyLabel}</span
					>
				</Command.Item>
			{/if}
			{#each listed as section, i (section.group ?? `ungrouped-${i}`)}
				<Command.Group heading={section.group ?? undefined}>
					{#each section.items as option (option.value)}
						{@const checked = picked(option.value)}
						<Command.Item
							value="{option.label} {option.hint ?? ''} {option.value}"
							disabled={option.disabled}
							onSelect={() => toggle(option)}
							class="cursor-pointer [&>.cn-command-item-indicator]:hidden"
							data-state={checked ? 'checked' : 'unchecked'}
						>
							<CheckIcon class={cn('size-4', checked ? 'opacity-100' : 'opacity-0')} />
							<!-- One line per option: the hint sits inline after the label rather than under
							     it, so a long list (a firm can hold 400+ DSR teams, or a 50-node tree) stays
							     scannable and its indentation still reads as a hierarchy. -->
							<span class="flex min-w-0 flex-1 items-baseline gap-2">
								<span
									class={cn(
										option.hint ? 'max-w-[65%] shrink-0 truncate' : 'min-w-0 truncate',
										'text-sm',
										monoLabel && 'font-mono text-xs'
									)}
									title={option.label}
								>
									{option.label}
								</span>
								{#if option.hint}
									<span
										class={cn(
											'text-muted-foreground min-w-0 flex-1 truncate text-xs',
											!monoLabel && 'font-mono'
										)}
										title={option.hint}
									>
										{option.hint}
									</span>
								{/if}
							</span>
							{#if option.count !== undefined}
								<span class="text-muted-foreground shrink-0 text-xs tabular-nums"
									>{option.count}</span
								>
							{/if}
						</Command.Item>
					{/each}
				</Command.Group>
			{/each}
		</Command.List>
	</Command.Root>
{/snippet}

{#if name}
	{#each value as selectedValue (selectedValue)}
		<input type="hidden" {name} value={selectedValue} />
	{/each}
{/if}

{#if inline}
	<div role="group" aria-label={ariaLabel}>
		{@render optionsList()}
	</div>
{:else}
	<Popover.Root
		bind:open
		onOpenChange={(next) => {
			open = next;
			onOpenChange?.(next);
		}}
	>
		<Popover.Trigger
			{id}
			{disabled}
			aria-invalid={invalid || undefined}
			aria-label={ariaLabel}
			class={cn(
				FIELD_CLASS,
				// Grows to fit its badges, which is the one way this differs from a single-line field:
				// `h-auto` replaces the shell's fixed height rather than fighting it.
				'flex h-auto min-h-8 items-center justify-between gap-2 py-1.5',
				className
			)}
		>
			<!-- `flex-nowrap` + `overflow-hidden`: the chips are a single clipped row, not a wrapping block.
		     Wrapping grows the trigger taller than its `h-9` neighbours the moment one value is long. -->
			<span class="flex min-w-0 flex-nowrap items-center gap-1 overflow-hidden">
				{#if selected.length === 0}
					<span class="text-muted-foreground">{placeholder}</span>
				{:else}
					{#each shown as option (option.value)}
						<!-- `min-w-0 shrink` overrides the Badge's own `shrink-0`: without it the chip keeps its
					     full intrinsic width, so the `truncate` below never fires and a long value spills
					     across the control beside it. -->
						<Badge variant="secondary" class="min-w-0 shrink gap-1 pr-1 font-normal">
							<span class={cn('truncate', monoLabel && 'font-mono text-xs')}>{option.label}</span>
							<!-- A span, not a button: this trigger is itself a button and
						     nesting interactive elements is invalid HTML. -->
							<span
								role="button"
								tabindex="-1"
								aria-label="Remove {option.label}"
								class="hover:bg-muted-foreground/20 rounded-sm p-0.5"
								onclick={(event) => {
									event.stopPropagation();
									remove(option.value);
								}}
								onkeydown={(event) => {
									if (event.key === 'Enter' || event.key === ' ') {
										event.preventDefault();
										event.stopPropagation();
										remove(option.value);
									}
								}}
							>
								<XIcon class="size-3" />
							</span>
						</Badge>
					{/each}
					{#if overflow > 0}
						<Badge variant="outline" class="font-normal">+{overflow} more</Badge>
					{/if}
				{/if}
			</span>
			<ChevronsUpDownIcon class="text-muted-foreground size-4 shrink-0 opacity-50" />
		</Popover.Trigger>
		<!-- `min-w`, not `w`: the panel matches the trigger as a FLOOR and may be wider, capped at the
	     viewport. A filter trigger is a chip a few characters wide, and pinning the panel to it made
	     every option truncate to `ac…` — the identifier is the thing being picked here, so it is the
	     panel that gives way, not the value. -->
		<Popover.Content
			class="min-w-(--bits-popover-anchor-width) w-auto max-w-[min(90vw,32rem)] p-0"
			align="start"
			portalProps={portal ? undefined : { disabled: true }}
		>
			{@render optionsList()}
		</Popover.Content>
	</Popover.Root>
{/if}
