<script lang="ts">
	import type { Form } from './types.js';
	import type { DocumentType } from '../templates/types.js';
	import { docTypeMeta } from '../templates/types.js';
	import { Badge } from '../components/ui/badge/index.js';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import CircleOffIcon from '@lucide/svelte/icons/circle-off';
	import { humanise } from '../sentence-case.js';

	// Single-select form picker: forms matching the template's document type are
	// suggested first; everything else sits behind "show all". `value` is the
	// selected form `code`, or null for "no input form". See docs/forms.md §9.1.
	let {
		forms = [],
		documentType = undefined,
		value = $bindable(null),
		allowNone = true
	}: {
		forms?: Form[];
		documentType?: DocumentType;
		value?: string | null;
		allowNone?: boolean;
	} = $props();

	const suggested = $derived(
		documentType ? forms.filter((f) => f.documentType === documentType) : []
	);
	const hasSuggested = $derived(suggested.length > 0);
	const mainList = $derived(hasSuggested ? suggested : forms);
	const extraList = $derived(hasSuggested ? forms.filter((f) => !suggested.includes(f)) : []);
	let showAll = $state(false);

	const optionCls = 'flex w-full items-center gap-3 rounded-lg border p-2.5 transition-colors';
	const selectedCls = 'border-ring bg-muted/40 ring-ring/30 ring-1';
	const idleCls = 'hover:border-ring/50 hover:bg-muted/20';
</script>

{#snippet option(form: Form)}
	{@const selected = value === form.code}
	<button
		type="button"
		class={[optionCls, selected ? selectedCls : idleCls]}
		onclick={() => (value = form.code)}
	>
		<span class="bg-brand/10 flex size-8 shrink-0 items-center justify-center rounded-lg">
			<ListChecksIcon class="text-brand size-4" />
		</span>
		<span class="min-w-0 flex-1 text-left">
			<span class="block truncate text-sm font-medium">{form.name}</span>
			<span class="text-muted-foreground block truncate text-xs">
				<span>{humanise(form.entityType)}</span> · {form.fieldCount} fields
			</span>
		</span>
		<Badge variant="outline" class="shrink-0">{humanise(form.status)}</Badge>
		{#if selected}<CheckIcon class="text-primary size-4 shrink-0" />{/if}
	</button>
{/snippet}

<div class="space-y-1.5">
	{#if forms.length === 0}
		<p class="text-muted-foreground rounded-lg border border-dashed py-6 text-center text-sm">
			No forms yet — create one in Forms first.
		</p>
	{:else}
		{#if hasSuggested && documentType}
			<p class="text-muted-foreground text-xs">Suggested for {docTypeMeta(documentType).label}</p>
		{/if}

		{#each mainList as form (form.uid)}
			{@render option(form)}
		{/each}

		{#if allowNone}
			<button
				type="button"
				class={[optionCls, value === null ? selectedCls : idleCls]}
				onclick={() => (value = null)}
			>
				<span class="bg-muted flex size-8 shrink-0 items-center justify-center rounded-lg">
					<CircleOffIcon class="text-muted-foreground size-4" />
				</span>
				<span class="min-w-0 flex-1 text-left">
					<span class="block text-sm font-medium">No input form</span>
					<span class="text-muted-foreground block text-xs">
						Advisers won't fill a form when generating.
					</span>
				</span>
				{#if value === null}<CheckIcon class="text-primary size-4 shrink-0" />{/if}
			</button>
		{/if}

		{#if extraList.length > 0}
			{#if showAll}
				<p class="text-muted-foreground pt-1 text-xs">Other forms</p>
				{#each extraList as form (form.uid)}
					{@render option(form)}
				{/each}
			{:else}
				<button
					type="button"
					class="text-muted-foreground hover:text-foreground w-full py-1 text-center text-xs underline"
					onclick={() => (showAll = true)}
				>
					Show all forms ({extraList.length})
				</button>
			{/if}
		{/if}
	{/if}
</div>
