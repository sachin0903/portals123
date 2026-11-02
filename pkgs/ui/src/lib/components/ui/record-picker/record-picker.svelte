<script lang="ts">
	// A searchable picker over records the caller fetches: type to search, click to choose, and
	// the chosen identifier posts in a hidden input.
	//
	// INLINE, NOT PORTALLED, and that is the whole reason it exists rather than `Combobox` or
	// `MultiSelect`. Those are built on `Popover`, whose content portals to `document.body` — put
	// one inside a `Sheet` and the panel renders outside the modal's focus trap, where it never
	// receives a click. This follows `AddressLookup`'s arrangement instead: an absolutely
	// positioned `role="listbox"` inside the field's own relative container, nothing portalled,
	// and selection on `mousedown` because `blur` fires before `click` and would close the list
	// first.
	//
	// The caller supplies `search`, so this knows nothing about where records come from.
	import { Input } from '../input/index.js';
	import { cn } from '../../../utils.js';
	import CheckIcon from '@lucide/svelte/icons/check';
	import Loader2Icon from '@lucide/svelte/icons/loader-circle';
	import SearchIcon from '@lucide/svelte/icons/search';
	import XIcon from '@lucide/svelte/icons/x';
	import type { RecordOption } from './types.js';

	let {
		value = $bindable(''),
		search,
		name,
		id,
		placeholder = 'Search…',
		emptyText = 'No matches.',
		capNote = '',
		/** What to show before a search, when `value` is already an id. An id is not a name. */
		label = '',
		disabled = false,
		openOnFocus = true,
		/** Draw the matches in the document flow. An overlay is clipped by a scrolling sheet. */
		inline = false,
		class: className,
		onSelect
	}: {
		/** The chosen identifier — what actually gets posted. */
		value?: string;
		/** Fetch matches for a query. Called on focus (empty query) and as the user types. */
		search: (query: string) => Promise<RecordOption[]>;
		/** Posts the identifier in a plain form submit. Omit for bind:value-only use. */
		name?: string;
		id?: string;
		placeholder?: string;
		emptyText?: string;
		/** Shown under the list when the source caps its results — "first 50 of many". */
		capNote?: string;
		label?: string;
		disabled?: boolean;
		openOnFocus?: boolean;
		inline?: boolean;
		class?: string;
		/** The chosen record, or null when the field is cleared. */
		onSelect?: (option: RecordOption | null) => void;
	} = $props();

	let text = $state('');
	let chosen = $state<RecordOption | null>(null);
	let options = $state<RecordOption[]>([]);
	let open = $state(false);
	let loading = $state(false);
	let activeIndex = $state(-1);
	let failed = $state('');
	let sequence = 0;

	// What the box shows: the chosen record's name once picked, otherwise whatever is being
	// typed. A value set from outside (a prefill) shows as itself rather than as an empty box.
	const shown = $derived(chosen ? chosen.label : text || (open ? '' : value ? label || value : ''));

	async function run(query: string) {
		// Out-of-order responses are the classic search bug: a slow "a" landing after a fast
		// "abc" replaces the right list with the wrong one.
		const mine = ++sequence;
		loading = true;
		failed = '';
		try {
			const found = await search(query);
			if (mine !== sequence) return;
			options = found;
			activeIndex = found.length ? 0 : -1;
		} catch (error) {
			if (mine !== sequence) return;
			options = [];
			// Stated, not swallowed: an empty list reads as "this firm has none", which is a
			// different and much more misleading answer than "the lookup failed".
			failed = error instanceof Error ? error.message : 'Lookup failed.';
		} finally {
			if (mine === sequence) loading = false;
		}
	}

	let timer: ReturnType<typeof setTimeout> | undefined;
	function onInput(next: string) {
		text = next;
		if (chosen || value) {
			chosen = null;
			value = '';
			onSelect?.(null);
		}
		open = true;
		clearTimeout(timer);
		timer = setTimeout(() => run(next), 200);
	}

	function choose(option: RecordOption) {
		chosen = option;
		value = option.value;
		text = '';
		open = false;
		onSelect?.(option);
	}

	function clear() {
		chosen = null;
		value = '';
		text = '';
		options = [];
		open = false;
		onSelect?.(null);
	}

	function onKeydown(event: KeyboardEvent) {
		if (!open) return;
		if (event.key === 'ArrowDown') {
			event.preventDefault();
			activeIndex = Math.min(activeIndex + 1, options.length - 1);
		} else if (event.key === 'ArrowUp') {
			event.preventDefault();
			activeIndex = Math.max(activeIndex - 1, 0);
		} else if (event.key === 'Enter' && activeIndex >= 0) {
			const option = options[activeIndex];
			if (!option) return;
			event.preventDefault();
			choose(option);
		} else if (event.key === 'Escape') {
			open = false;
		}
	}
</script>

<div class={cn('relative', open && 'z-50', className)}>
	<div class="relative">
		<SearchIcon
			class="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2"
		/>
		<Input
			{id}
			type="text"
			class="pr-8 pl-8"
			value={shown}
			{placeholder}
			{disabled}
			autocomplete="off"
			role="combobox"
			aria-expanded={open}
			aria-autocomplete="list"
			oninput={(event) => onInput(event.currentTarget.value)}
			onfocus={() => {
				if (!openOnFocus || disabled) return;
				open = true;
				if (!options.length) run('');
			}}
			onblur={() => (open = false)}
			onkeydown={onKeydown}
		/>
		{#if loading}
			<Loader2Icon
				class="text-muted-foreground absolute top-1/2 right-2.5 size-3.5 -translate-y-1/2 animate-spin"
			/>
		{:else if value}
			<button
				type="button"
				class="text-muted-foreground hover:text-foreground absolute top-1/2 right-2 -translate-y-1/2"
				aria-label="Clear"
				onclick={clear}
			>
				<XIcon class="size-3.5" />
			</button>
		{/if}
	</div>

	<!-- The identifier, not the label: what the vendor is sent. -->
	{#if name}
		<input type="hidden" {name} {value} />
	{/if}

	{#if open}
		<ul
			role="listbox"
			class={cn(
				'bg-popover z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-md border p-1 shadow-md',
				inline ? 'relative' : 'absolute'
			)}
		>
			{#if failed}
				<li class="text-destructive px-2 py-1.5 text-xs">{failed}</li>
			{:else if !options.length && !loading}
				<li class="text-muted-foreground px-2 py-1.5 text-xs">{emptyText}</li>
			{/if}
			{#each options as option, index (option.value)}
				<li role="option" aria-selected={option.value === value}>
					<button
						type="button"
						class={cn(
							'flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm',
							index === activeIndex && 'bg-accent text-accent-foreground'
						)}
						onmousedown={(event) => event.preventDefault()}
						onclick={() => choose(option)}
					>
						<CheckIcon
							class={cn('mt-0.5 size-3.5 shrink-0', option.value !== value && 'invisible')}
						/>
						<span class="min-w-0">
							<span class="block truncate">{option.label}</span>
							{#if option.hint}
								<span class="text-muted-foreground block truncate text-xs">{option.hint}</span>
							{/if}
						</span>
					</button>
				</li>
			{/each}
			{#if capNote && options.length}
				<!-- Said out loud: "it isn't in the list" must not be read as "it does not exist". -->
				<li class="text-muted-foreground border-t px-2 py-1.5 text-xs">{capNote}</li>
			{/if}
		</ul>
	{/if}
</div>
