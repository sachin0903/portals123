<script lang="ts">
	import * as Popover from '../../components/ui/popover/index.js';
	import { cn } from '../../utils.js';
	import { FIELD_CLASS } from '../../field-shell.js';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ChevronsUpDownIcon from '@lucide/svelte/icons/chevrons-up-down';
	import type { ResolvedOption } from '../editor/options';

	let {
		value = '',
		onchange,
		options = [],
		placeholder = 'Select…',
		invalid = false,
		id
	}: {
		value?: unknown;
		onchange: (v: string) => void;
		options?: ResolvedOption[];
		placeholder?: string;
		invalid?: boolean;
		id?: string;
	} = $props();

	let open = $state(false);
	let query = $state('');

	const selectedLabel = $derived(options.find((o) => o.value === String(value ?? ''))?.label ?? '');
	const filtered = $derived(
		query.trim()
			? options.filter((o) => o.label.toLowerCase().includes(query.trim().toLowerCase()))
			: options
	);

	function pick(v: string) {
		onchange(v);
		open = false;
		query = '';
	}
</script>

<Popover.Root bind:open>
	<Popover.Trigger
		{id}
		class={cn(FIELD_CLASS, 'flex items-center justify-between')}
		aria-invalid={invalid || undefined}
	>
		<span
			class={['min-w-0 truncate', selectedLabel ? '' : 'text-muted-foreground']}
			title={selectedLabel || undefined}>{selectedLabel || placeholder}</span
		>
		<ChevronsUpDownIcon class="size-4 shrink-0 opacity-50" />
	</Popover.Trigger>
	<Popover.Content
		align="start"
		class="w-(--bits-popover-anchor-width) max-w-[min(90vw,28rem)] min-w-56 p-0"
	>
		<div class="border-b p-2">
			<!-- svelte-ignore a11y_autofocus -->
			<input
				bind:value={query}
				placeholder="Search…"
				autofocus
				class="placeholder:text-muted-foreground w-full bg-transparent text-sm outline-none"
			/>
		</div>
		<div class="max-h-56 overflow-y-auto p-1">
			{#each filtered as opt (opt.value)}
				<!-- The whole label, wrapped: a model name is read in full here or not chosen at
				     all, and the list is the one place it can be read before choosing. The trigger
				     above still truncates (one line of a field) and carries the title. -->
				<button
					type="button"
					class="hover:bg-accent hover:text-accent-foreground flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm"
					onclick={() => pick(opt.value)}
				>
					<CheckIcon
						class={[
							'mt-0.5 size-4 shrink-0',
							opt.value === String(value ?? '') ? 'opacity-100' : 'opacity-0'
						]}
					/>
					<span class="min-w-0 break-words whitespace-normal">{opt.label}</span>
				</button>
			{:else}
				<p class="text-muted-foreground px-2 py-6 text-center text-sm">No results.</p>
			{/each}
		</div>
	</Popover.Content>
</Popover.Root>
