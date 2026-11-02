<script lang="ts">
	// ONE FILTER'S VOCABULARY, in the panel that already opened. The list is the editor: a tick
	// on each chosen value, the count of rows beside it. A second dropdown inside that panel
	// (the old searchable multi-select, or a select for a single choice) made every filter a
	// two-stage control. Search appears once the list is long enough to need it, or immediately
	// when the field is an autocomplete — those hide the list until something is typed.
	import { MultiSelect } from '../../ui/multi-select/index.js';
	import type { MultiSelectOption } from '../../ui/multi-select/types.js';
	import { FILTER_OPTION_SEARCH_AT } from './filters.js';

	let {
		options,
		value = [],
		single = false,
		/** The unset row's label on a `single` filter, e.g. "All firms". Default "Any". */
		allLabel,
		label,
		searchPlaceholder,
		autocomplete = false,
		onchange
	}: {
		options: readonly MultiSelectOption[];
		value?: string[];
		single?: boolean;
		allLabel?: string;
		label: string;
		searchPlaceholder?: string;
		autocomplete?: boolean;
		onchange: (next: string[]) => void;
	} = $props();

	const searchable = $derived(autocomplete || options.length >= FILTER_OPTION_SEARCH_AT);
</script>

<MultiSelect
	inline
	{searchable}
	{autocomplete}
	{single}
	emptyLabel={single ? (allLabel ?? 'Any') : undefined}
	options={[...options]}
	{value}
	searchPlaceholder={searchPlaceholder ?? `Search ${label.toLowerCase()}…`}
	aria-label={label}
	onValueChange={onchange}
/>
