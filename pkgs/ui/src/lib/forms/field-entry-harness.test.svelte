<script lang="ts">
	// The host a real page is: it owns `values` as `$state` and binds it, so the sweep in
	// `field-entry.dom.test.ts` reads back what an adviser's answers would actually be. A plain
	// object passed from a `.test.ts` is not the same thing — `$state` cannot be declared outside a
	// Svelte file, and without it nothing the form writes is reactive and the test proves nothing.
	//
	// `.test.svelte` so the package's lint/check globs treat it as test material.
	import { untrack } from 'svelte';
	import DynamicForm from './dynamic-form.svelte';
	import type { FormField } from './types.js';

	let {
		fields,
		dataSources = {},
		initial = {},
		sectionNav = false
	}: {
		fields: FormField[];
		dataSources?: Record<string, unknown[]>;
		initial?: Record<string, unknown>;
		sectionNav?: boolean;
	} = $props();

	let form = $state<{ validate: () => boolean } | undefined>();

	// Seeded once, like the step page does it: `initial` is a prop and reading it here is a
	// one-time copy, which is what a form's starting answers are.
	let values = $state<Record<string, unknown>>(untrack(() => ({ ...initial })));

	/** Write a value the way another control on the form would. */
	export function set(key: string, v: unknown) {
		values[key] = v;
	}
	/** The answers, snapshotted out of the proxy. */
	export function read(): Record<string, unknown> {
		return { ...$state.snapshot(values) };
	}
	/** Press Save, the way the page's own submit does. */
	export function submit(): boolean {
		return form?.validate() ?? false;
	}
</script>

<DynamicForm bind:this={form} {fields} {dataSources} {sectionNav} bind:values />
