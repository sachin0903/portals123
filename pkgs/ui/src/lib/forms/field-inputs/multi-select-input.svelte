<script lang="ts">
	// The form's multi-select is the shared control. A second popover here used a checkbox
	// for each option, so nationality and tax residency on a fact-find looked unlike every
	// other searchable multi-select, which marks a choice with a tick.
	import { MultiSelect } from '../../components/ui/multi-select/index.js';
	import type { ResolvedOption } from '../editor/options';

	let {
		value = [],
		onchange,
		options = [],
		placeholder = 'Select…',
		invalid = false,
		id
	}: {
		value?: unknown;
		onchange: (v: string[]) => void;
		options?: ResolvedOption[];
		placeholder?: string;
		invalid?: boolean;
		id?: string;
	} = $props();

	function normalize(v: unknown): string[] {
		if (Array.isArray(v)) return v.map((x) => String(x)).filter(Boolean);
		if (typeof v === 'string')
			return v
				.split(',')
				.map((x) => x.trim())
				.filter(Boolean);
		return [];
	}

	const selected = $derived(normalize(value));
</script>

<MultiSelect {id} {options} {placeholder} value={selected} onValueChange={onchange} {invalid} />
