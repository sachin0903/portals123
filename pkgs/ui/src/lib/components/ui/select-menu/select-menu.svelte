<script lang="ts" module>
	export interface SelectMenuOption {
		value: string;
		label: string;
		disabled?: boolean;
	}
	export interface SelectMenuGroup {
		/** Omitted when the group is only a band of options, split from the next by `dividers`. */
		label?: string;
		options: SelectMenuOption[];
	}
	export type SelectMenuItems = SelectMenuOption[] | SelectMenuGroup[];

	function isGrouped(items: SelectMenuItems): items is SelectMenuGroup[] {
		const first = items[0];
		return first !== undefined && 'options' in first;
	}
</script>

<script lang="ts">
	import * as Select from '../select/index.js';
	import { cn } from '../../../utils.js';

	/**
	 * The one-line dropdown for the common case — a styled replacement for a native `<select>`.
	 * Wraps the compound Select (bits-ui) and, when `name` is given, renders a hidden input so
	 * plain form POSTs (use:enhance actions) keep working exactly like the native control did.
	 * Reach for the compound `Select.*` parts directly when you need custom item rendering.
	 */
	let {
		items,
		value = $bindable(),
		placeholder = 'Choose…',
		name,
		id,
		disabled = false,
		required = false,
		/** Portal the panel to `document.body` (the default). Turn off inside a Dialog or Sheet:
		 *  a portalled panel sits outside that modal's focus trap and never receives a click. */
		portal = true,
		size = 'default',
		/** Accessible name. The trigger shows the current value, which is not always the question. */
		label,
		/** A rule between groups, instead of a heading on each. Groups then need no label. */
		dividers = false,
		/** Extra classes on each option. The item's own `text-sm` yields to a size passed here. */
		itemClass,
		class: className,
		onValueChange,
		onOpenChange
	}: {
		items: SelectMenuItems;
		value?: string;
		placeholder?: string;
		/** Form field name — renders a hidden input carrying the selection for form actions. */
		name?: string;
		id?: string;
		disabled?: boolean;
		required?: boolean;
		portal?: boolean;
		size?: 'sm' | 'default';
		label?: string;
		dividers?: boolean;
		itemClass?: string;
		class?: string;
		onValueChange?: (value: string) => void;
		/** Fired when the menu opens or closes — lazy pickers load their options on open. */
		onOpenChange?: (open: boolean) => void;
	} = $props();

	const flat = $derived(isGrouped(items) ? items.flatMap((g) => g.options) : items);
	const selectedLabel = $derived(flat.find((o) => o.value === value)?.label);
</script>

<Select.Root
	type="single"
	bind:value
	{disabled}
	{required}
	onValueChange={(v) => onValueChange?.(v as string)}
	onOpenChange={(next) => onOpenChange?.(next)}
>
	<Select.Trigger
		{id}
		{size}
		class={cn('w-full', className)}
		aria-label={label}
		aria-required={required}
	>
		<!-- Two spans, because the trigger forces `display:flex` on [data-slot=select-value] and
		     `truncate` does nothing to a flex CONTAINER — it has to sit on the text's own box. The
		     outer one takes `min-w-0` so it may shrink below its content (a flex item defaults to
		     min-width:auto and would otherwise push the chevron out of the trigger). Without both,
		     a long option name — a model called "Balanced Growth (Sustainable, Higher Risk)" — runs
		     past the control instead of ellipsing. -->
		<span class="min-w-0" data-slot="select-value">
			<!-- `title` so a clipped name is still readable on hover, rather than only discoverable
			     by opening the menu again. -->
			<span
				class={cn('truncate', selectedLabel ? '' : 'text-muted-foreground')}
				title={selectedLabel ?? undefined}
			>
				{selectedLabel ?? placeholder}
			</span>
		</span>
	</Select.Trigger>
	<Select.Content portalProps={portal ? undefined : { disabled: true }}>
		{#if isGrouped(items)}
			{#each items as group, i (group.label ?? group.options[0]?.value ?? i)}
				{#if dividers && i > 0}
					<Select.Separator />
				{/if}
				<Select.Group>
					{#if group.label}
						<Select.GroupHeading>{group.label}</Select.GroupHeading>
					{/if}
					{#each group.options as o (o.value)}
						<Select.Item class={itemClass} value={o.value} label={o.label} disabled={o.disabled} />
					{/each}
				</Select.Group>
			{/each}
		{:else}
			{#each items as o (o.value)}
				<Select.Item class={itemClass} value={o.value} label={o.label} disabled={o.disabled} />
			{/each}
		{/if}
	</Select.Content>
</Select.Root>
{#if name}
	<input type="hidden" {name} value={value ?? ''} />
{/if}
