<script lang="ts">
	import type { HTMLInputAttributes, HTMLInputTypeAttribute } from 'svelte/elements';
	import { cn, type WithElementRef } from '../../../utils.js';
	// The shell is shared with every other field-shaped control (see field-shell.ts): the class
	// string used to be written out here twice, and copied again into Combobox and MultiSelect,
	// which is how their backgrounds drifted apart from this one.
	import { FIELD_CLASS } from '../../../field-shell.js';

	type InputType = Exclude<HTMLInputTypeAttribute, 'file'>;

	type Props = WithElementRef<
		Omit<HTMLInputAttributes, 'type'> &
			({ type: 'file'; files?: FileList } | { type?: InputType; files?: undefined })
	>;

	let {
		ref = $bindable(null),
		value = $bindable(),
		type,
		files = $bindable(),
		class: className,
		'data-slot': dataSlot = 'input',
		...restProps
	}: Props = $props();

	const PLACEHOLDER_CLASS = 'placeholder:text-muted-foreground';
	/** The file picker's own button, which no other field has. */
	const FILE_CLASS =
		'file:text-foreground file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium';
</script>

{#if type === 'file'}
	<input
		bind:this={ref}
		data-slot={dataSlot}
		class={cn(FIELD_CLASS, FILE_CLASS, className)}
		type="file"
		bind:files
		bind:value
		{...restProps}
	/>
{:else}
	<input
		bind:this={ref}
		data-slot={dataSlot}
		class={cn(FIELD_CLASS, PLACEHOLDER_CLASS, className)}
		{type}
		bind:value
		{...restProps}
	/>
{/if}
