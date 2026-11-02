<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { Checkbox } from '../checkbox/index.js';
	import * as Popover from '../popover/index.js';
	import { cn } from '../../../utils.js';
	import { buttonVariants } from '../button/index.js';

	export type RolePickerOption = {
		value: string;
		label: string;
		description?: string;
	};

	let {
		options = [],
		value = $bindable([]),
		onchange,
		disabled = false,
		allowEmpty = false,
		placeholder,
		maxVisible = 2,
		ariaLabel = 'Edit roles',
		class: className
	}: {
		options?: RolePickerOption[];
		value?: string[];
		onchange?: (value: string[]) => void;
		disabled?: boolean;
		/** Permit dropping to zero roles. A team membership keeps at least one (the default);
		 *  an action policy allows none (empty = "anyone" / "no approval"). */
		allowEmpty?: boolean;
		/** Shown in place of chips when nothing is selected (only meaningful with `allowEmpty`). */
		placeholder?: string;
		maxVisible?: number;
		ariaLabel?: string;
		class?: string;
	} = $props();

	const byValue = $derived(new Map(options.map((option) => [option.value, option])));
	const selected = $derived(value.map((slug) => byValue.get(slug) ?? { value: slug, label: slug }));

	// A stable semantic sequence makes roles distinguishable without introducing raw palette colours
	// that bypass a firm's theme. The text remains the meaning; colour is only a scanning aid.
	const dots = ['bg-primary', 'bg-success', 'bg-warning', 'bg-destructive'] as const;
	const dotFor = (slug: string) => {
		const total = [...slug].reduce((sum, char) => sum + char.charCodeAt(0), 0);
		return dots[total % dots.length];
	};

	function toggle(slug: string, checked: boolean): void {
		const next = checked
			? [...new Set([...value, slug])]
			: value.filter((candidate) => candidate !== slug);
		// A membership must retain at least one role (keeping the last checkbox on communicates that
		// rule at the point of action); an `allowEmpty` picker — an action policy — may drop to none.
		if (next.length === 0 && !allowEmpty) return;
		value = next;
		onchange?.(next);
	}
</script>

<div class={cn('flex min-w-0 items-center gap-1.5', className)}>
	<div class="flex min-w-0 flex-wrap items-center gap-1">
		{#if selected.length === 0 && placeholder}
			<span class="text-muted-foreground text-[11px]">{placeholder}</span>
		{/if}
		{#each selected.slice(0, maxVisible) as role (role.value)}
			<span
				class="border-border/70 bg-muted/65 inline-flex h-6 max-w-40 items-center gap-1.5 rounded-md border px-2 text-[11px] font-medium"
				title={role.label}
			>
				<span class={cn('size-2 shrink-0 rounded-full', dotFor(role.value))}></span>
				<span class="truncate">{role.label}</span>
			</span>
		{/each}
		{#if selected.length > maxVisible}
			<span class="text-muted-foreground px-1 text-[11px] font-medium">
				+{selected.length - maxVisible}
			</span>
		{/if}
	</div>

	<Popover.Root>
		<Popover.Trigger
			{disabled}
			aria-label={ariaLabel}
			title={ariaLabel}
			class={cn(
				buttonVariants({ variant: 'ghost', size: 'icon-xs' }),
				'border-border/70 text-muted-foreground border border-dashed'
			)}
		>
			<PlusIcon />
		</Popover.Trigger>
		<Popover.Content align="end" class="w-72 gap-0 p-1.5">
			<div class="px-2 pt-1.5 pb-2">
				<p class="text-xs font-semibold">Assign roles</p>
				<p class="text-muted-foreground mt-0.5 text-[11px]">Changes save immediately.</p>
			</div>
			<div class="no-scrollbar max-h-72 overflow-y-auto">
				{#each options as option (option.value)}
					{@const checked = value.includes(option.value)}
					<label
						class="hover:bg-muted/70 flex min-h-9 cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5"
					>
						<span class={cn('size-2 shrink-0 rounded-full', dotFor(option.value))}></span>
						<span class="min-w-0 flex-1">
							<span class="block truncate text-xs font-medium">{option.label}</span>
							{#if option.description}
								<span class="text-muted-foreground block truncate text-[10px]">
									{option.description}
								</span>
							{/if}
						</span>
						<Checkbox
							{checked}
							disabled={checked && value.length === 1 && !allowEmpty}
							aria-label={`${checked ? 'Remove' : 'Add'} ${option.label}`}
							onCheckedChange={(next) => toggle(option.value, next === true)}
						/>
					</label>
				{/each}
			</div>
		</Popover.Content>
	</Popover.Root>
</div>
