<script lang="ts">
	// ONE field's editor, debounced only where a keystroke is the edit. A number typed into a
	// range used to navigate on every digit; a tick on a choice is already one gesture and
	// should not then wait.
	import { untrack } from 'svelte';
	import FilterChoices from './filter-choices.svelte';
	import FilterRange from './filter-range.svelte';
	import type { FilterDef } from './filter-panel.svelte';

	let {
		filter,
		value = [],
		onchange,
		debounce = 300
	}: {
		filter: FilterDef;
		value?: string[];
		onchange: (next: string[]) => void;
		debounce?: number;
	} = $props();

	let pending = $state<string[]>(untrack(() => [...value]));
	let sent = untrack(() => [...value]);
	let timer: ReturnType<typeof setTimeout> | undefined;
	// Plain flag, not state. Hiding the bar, or clearing the tag, unmounts this editor before
	// its click handler runs, and a state write on a component already being destroyed does not
	// stick — the teardown would still flush the half-typed amount.
	let dropped = false;

	function same(left: readonly string[], right: readonly string[]): boolean {
		return left.length === right.length && left.every((item, i) => item === right[i]);
	}

	function flush(): void {
		clearTimeout(timer);
		timer = undefined;
		if (dropped || same(pending, sent)) return;
		sent = [...pending];
		onchange([...pending]);
	}

	/** Forget a half-typed edit. Dismiss and Clear unmount this editor, and the destroy flush
	 *  would otherwise write `pending` back after the key had already been removed. */
	export function discard(): void {
		dropped = true;
		clearTimeout(timer);
		timer = undefined;
		pending = [...sent];
	}

	function commit(next: string[]): void {
		pending = next;
		if (filter.kind === 'numberrange') {
			clearTimeout(timer);
			timer = setTimeout(flush, debounce);
			return;
		}
		flush();
	}

	/** A clear from the tag, or a value arriving from the URL, re-seeds. Our own write echoes
	 *  back as `value` and must not throw away a digit typed since. */
	$effect(() => {
		if (same(value, sent)) return;
		sent = [...value];
		pending = [...value];
	});

	$effect(() => () => flush());
</script>

{#if filter.kind === 'numberrange'}
	<FilterRange
		label={filter.label}
		value={pending}
		min={filter.min}
		max={filter.max}
		currency={filter.currency}
		onchange={commit}
	/>
{:else if filter.kind !== 'daterange'}
	<FilterChoices
		options={filter.options}
		value={pending}
		single={filter.single}
		allLabel={filter.allLabel}
		label={filter.label}
		searchPlaceholder={filter.searchPlaceholder}
		autocomplete={filter.autocomplete}
		onchange={commit}
	/>
{/if}
