<script lang="ts">
	// Address autocomplete over Postcoder, ported from the React component in
	// graphene-platform (`pkgs/components/src/forms/postcoder`).
	//
	// The endpoint is the portal's same-origin BFF (`/api/postcoder/[action]`), which forwards to
	// the API's `/ref/postcoder/{action}` with the user's bearer. The Postcoder key lives only on
	// the API — the upstream takes it as a query parameter, so a browser call would publish it.
	//
	// The one non-obvious bit of the Postcoder flow is that `find` returns two kinds of thing.
	// A suggestion whose `type` is not `ADD` is a CONTAINER — a street, building or town — and
	// asking `retrieve` for it fails. Selecting one has to call `find` again with `pathfilter`
	// set to its id, which drills in a level. So a click either narrows the list or completes
	// the address, depending on what was clicked.
	import { Input } from '../input/index.js';
	import { cn } from '../../../utils.js';
	import Loader2Icon from '@lucide/svelte/icons/loader-circle';
	import MapPinIcon from '@lucide/svelte/icons/map-pin';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import {
		formatAddress,
		isAddressSuggestionContainer,
		normaliseAddress,
		type Address,
		type AddressSuggestion
	} from './types.js';

	let {
		value = $bindable(null),
		country = 'GB',
		endpoint = '/api/postcoder',
		label = 'Search for an address or postcode',
		placeholder = 'Start typing an address or postcode…',
		disabled = false,
		invalid = false,
		id,
		class: className
	}: {
		/** The chosen address. Bindable so a form can read it and pre-fill it. */
		value?: Address | null;
		/** ISO-2 country the search is scoped to. */
		country?: string;
		/** Where the app mounted the proxy. */
		endpoint?: string;
		label?: string;
		placeholder?: string;
		disabled?: boolean;
		invalid?: boolean;
		id?: string;
		class?: string;
	} = $props();

	let query = $state('');
	let suggestions = $state<AddressSuggestion[]>([]);
	let open = $state(false);
	let loading = $state(false);
	let failure = $state<string | null>(null);
	let activeIndex = $state(-1);

	// One in-flight request at a time: typing fires a lot of them and they can land out of
	// order, which would show suggestions for a prefix the box no longer contains.
	let inFlight: AbortController | null = null;
	let debounce: ReturnType<typeof setTimeout> | null = null;

	const MIN_QUERY = 3;

	// A pre-filled form should show the chosen address in the search control. Typing clears
	// `value` first, so this never fights a new search.
	$effect(() => {
		if (value && !query) query = formatAddress(value);
	});

	async function find(text: string, filter: string | null): Promise<void> {
		inFlight?.abort();
		const controller = new AbortController();
		inFlight = controller;
		loading = true;
		failure = null;
		try {
			const url = new URL(`${endpoint}/find`, window.location.origin);
			url.searchParams.set('query', text);
			url.searchParams.set('country', country);
			if (filter) url.searchParams.set('pathfilter', filter);
			const response = await fetch(url, { signal: controller.signal });
			if (!response.ok) {
				const body = await response.json().catch(() => null);
				failure = body?.message ?? 'Address lookup is unavailable.';
				suggestions = [];
				return;
			}
			suggestions = (await response.json()) as AddressSuggestion[];
			activeIndex = -1;
			open = true;
		} catch (error) {
			// An abort is this component superseding itself, not a failure worth showing.
			if ((error as Error)?.name === 'AbortError') return;
			failure = 'Could not reach the address lookup service.';
			suggestions = [];
		} finally {
			if (inFlight === controller) loading = false;
		}
	}

	function onInput(): void {
		// Typing after a drill-down starts a fresh top-level search, which is what someone
		// correcting a wrong street expects — the container filter is per-click, not sticky.
		value = null;
		if (debounce) clearTimeout(debounce);
		const text = query.trim();
		if (text.length < MIN_QUERY) {
			suggestions = [];
			open = false;
			return;
		}
		debounce = setTimeout(() => void find(text, null), 250);
	}

	async function choose(suggestion: AddressSuggestion): Promise<void> {
		// A container narrows the search rather than completing it.
		if (isAddressSuggestionContainer(suggestion)) {
			await find(query.trim(), suggestion.id);
			return;
		}
		loading = true;
		failure = null;
		try {
			const url = new URL(`${endpoint}/retrieve`, window.location.origin);
			url.searchParams.set('id', suggestion.id);
			url.searchParams.set('country', country);
			if (query.trim()) url.searchParams.set('query', query.trim());
			const response = await fetch(url);
			if (!response.ok) {
				failure = 'Could not retrieve that address.';
				return;
			}
			const payload = await response.json();
			const first = Array.isArray(payload) ? payload[0] : payload;
			if (!first) {
				failure = 'That address came back empty.';
				return;
			}
			value = normaliseAddress(first, country);
			query = formatAddress(value);
			suggestions = [];
			open = false;
		} finally {
			loading = false;
		}
	}

	function onKeydown(event: KeyboardEvent): void {
		if (!open || !suggestions.length) return;
		if (event.key === 'ArrowDown') {
			event.preventDefault();
			activeIndex = (activeIndex + 1) % suggestions.length;
		} else if (event.key === 'ArrowUp') {
			event.preventDefault();
			activeIndex = activeIndex <= 0 ? suggestions.length - 1 : activeIndex - 1;
		} else if (event.key === 'Enter' && activeIndex >= 0) {
			event.preventDefault();
			const chosen = suggestions[activeIndex];
			if (chosen) void choose(chosen);
		} else if (event.key === 'Escape') {
			open = false;
		}
	}

	/** Clear the chosen address and start again. */
	export function reset(): void {
		value = null;
		query = '';
		suggestions = [];
		open = false;
	}
</script>

<div class={cn('relative', open && 'z-50', className)}>
	<div class="relative">
		<Input
			{id}
			bind:value={query}
			{placeholder}
			{disabled}
			aria-invalid={invalid}
			aria-label={label}
			aria-expanded={open}
			aria-autocomplete="list"
			role="combobox"
			autocomplete="off"
			oninput={onInput}
			onkeydown={onKeydown}
			onblur={() => setTimeout(() => (open = false), 150)}
			onfocus={() => suggestions.length && (open = true)}
		/>
		{#if loading}
			<Loader2Icon
				class="text-muted-foreground absolute top-1/2 right-3 size-4 -translate-y-1/2 animate-spin"
			/>
		{/if}
	</div>

	{#if failure}
		<p class="text-destructive mt-1 text-xs">{failure}</p>
	{:else if query.trim().length > 0 && query.trim().length < MIN_QUERY && !value}
		<p class="text-muted-foreground mt-1 text-xs">Keep typing — {MIN_QUERY} characters or more.</p>
	{/if}

	{#if open && suggestions.length}
		<ul
			role="listbox"
			aria-label="Address suggestions"
			class="bg-popover absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-md border shadow-md"
		>
			{#each suggestions as suggestion, index (suggestion.id)}
				<li role="option" aria-selected={index === activeIndex}>
					<button
						type="button"
						class={cn(
							'flex w-full items-center gap-2 px-3 py-2 text-left text-sm',
							index === activeIndex ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
						)}
						onmousedown={(event) => event.preventDefault()}
						onclick={() => choose(suggestion)}
					>
						<MapPinIcon class="text-muted-foreground size-3.5 shrink-0" />
						<span class="min-w-0 flex-1 truncate">
							{suggestion.summaryline}
							{#if suggestion.locationsummary}
								<span class="text-muted-foreground"> · {suggestion.locationsummary}</span>
							{/if}
						</span>
						{#if isAddressSuggestionContainer(suggestion)}
							<!-- A container: choosing it drills in rather than filling the form. -->
							<span class="text-muted-foreground flex shrink-0 items-center gap-0.5 text-xs">
								{suggestion.count}
								<ChevronRightIcon class="size-3" />
							</span>
						{/if}
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>
