<script lang="ts">
	import {
		Dialog,
		SelectMenu,
		Button,
		Input,
		StatusBadge,
		FORM_STATUS_META,
		DOC_TYPE_META,
		type Form,
		type DocumentType,
		type FormStatus,
		type FormEntityType
	} from '@graphene/ui';
	import SearchIcon from '@lucide/svelte/icons/search';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import CheckIcon from '@lucide/svelte/icons/check';
	import CircleOffIcon from '@lucide/svelte/icons/circle-off';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import { humanise } from '../../sentence-case.js';

	// Larger, filterable single-select form picker (docs/forms.md §9.1). Replaces the
	// narrow sheet so a substantial form library stays browsable. Selection is local
	// until Save, which fires `onsave` with the chosen form code (or null for none).
	let {
		open = $bindable(false),
		forms = [],
		documentType = undefined,
		value = null,
		onsave
	}: {
		open?: boolean;
		forms?: Form[];
		documentType?: DocumentType;
		value?: string | null;
		onsave?: (code: string | null) => void;
	} = $props();

	let search = $state('');
	let docFilter = $state<'all' | DocumentType>('all');
	let entityFilter = $state<'all' | FormEntityType>('all');
	let statusFilter = $state<'all' | FormStatus>('all');
	let selected = $state<string | null>(null);

	const DOC_TYPES = Object.entries(DOC_TYPE_META) as [DocumentType, { label: string }][];
	const STATUSES = Object.entries(FORM_STATUS_META) as [FormStatus, { label: string }][];

	// Reset local state each time the dialog opens.
	let lastOpen = false;
	$effect(() => {
		if (open && !lastOpen) {
			selected = value;
			search = '';
			docFilter = 'all';
			entityFilter = 'all';
			statusFilter = 'all';
		}
		lastOpen = open;
	});

	const filtered = $derived(
		forms
			.filter((f) => {
				const q = search.trim().toLowerCase();
				if (q && !`${f.name} ${f.code}`.toLowerCase().includes(q)) return false;
				if (docFilter !== 'all' && f.documentType !== docFilter) return false;
				if (entityFilter !== 'all' && f.entityType !== entityFilter) return false;
				if (statusFilter !== 'all' && f.status !== statusFilter) return false;
				return true;
			})
			// Forms matching this template's document type float to the top.
			.sort((a, b) => {
				const ar = documentType && a.documentType === documentType ? 0 : 1;
				const br = documentType && b.documentType === documentType ? 0 : 1;
				return ar - br;
			})
	);

	const baseOpt = 'flex items-center gap-3 rounded-xl border p-3 text-left transition-all';
	const selOpt = 'border-ring bg-muted/40 ring-ring/30 ring-1';
	const idleOpt = 'hover:border-ring/50 hover:bg-muted/20';

	function save() {
		onsave?.(selected);
		open = false;
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="flex max-h-[85vh] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
		<Dialog.Header class="space-y-1 border-b px-6 py-4 text-left">
			<Dialog.Title class="flex items-center gap-2">
				<ListChecksIcon class="text-primary size-4" /> Choose input form
			</Dialog.Title>
			<Dialog.Description>
				The form advisers fill in when generating this template — one form per template.
			</Dialog.Description>
		</Dialog.Header>

		<!-- Filter toolbar -->
		<div class="flex flex-wrap items-center gap-2 border-b px-6 py-3">
			<div class="relative min-w-[12rem] flex-1">
				<SearchIcon
					class="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
				/>
				<Input placeholder="Search forms…" class="pl-9" bind:value={search} />
			</div>
			<SelectMenu
				class="w-40"
				items={[
					{ value: 'all', label: 'All types' },
					...DOC_TYPES.map(([key, meta]) => ({ value: key, label: meta.label }))
				]}
				bind:value={docFilter}
			/>
			<SelectMenu
				class="w-40"
				items={[
					{ value: 'all', label: 'All entities' },
					{ value: 'applicant', label: 'Applicant' },
					{ value: 'account', label: 'Account' }
				]}
				bind:value={entityFilter}
			/>
			<SelectMenu
				class="w-40"
				items={[
					{ value: 'all', label: 'All statuses' },
					...STATUSES.map(([key, meta]) => ({ value: key, label: meta.label }))
				]}
				bind:value={statusFilter}
			/>
		</div>

		<!-- Options -->
		<div class="min-h-[16rem] flex-1 overflow-y-auto px-6 py-4">
			{#if forms.length === 0}
				<div
					class="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 py-12 text-center text-sm"
				>
					<ListChecksIcon class="size-8 opacity-40" />
					No forms yet — create one in Forms first.
				</div>
			{:else}
				<div class="grid gap-2.5 sm:grid-cols-2">
					<!-- No form -->
					<button
						type="button"
						class={[baseOpt, selected === null ? selOpt : idleOpt]}
						onclick={() => (selected = null)}
					>
						<span class="bg-muted flex size-9 shrink-0 items-center justify-center rounded-lg">
							<CircleOffIcon class="text-muted-foreground size-4.5" />
						</span>
						<span class="min-w-0 flex-1">
							<span class="block text-sm font-medium">No input form</span>
							<span class="text-muted-foreground block text-xs">Advisers won't fill a form.</span>
						</span>
						{#if selected === null}<CheckIcon class="text-primary size-4 shrink-0" />{/if}
					</button>

					{#each filtered as form (form.uid)}
						{@const fm = FORM_STATUS_META[form.status]}
						{@const isSuggested = documentType && form.documentType === documentType}
						<button
							type="button"
							class={[baseOpt, selected === form.code ? selOpt : idleOpt]}
							onclick={() => (selected = form.code)}
						>
							<span class="bg-brand/10 flex size-9 shrink-0 items-center justify-center rounded-lg">
								<ListChecksIcon class="text-brand size-4.5" />
							</span>
							<span class="min-w-0 flex-1">
								<span class="flex items-center gap-1.5">
									<span class="truncate text-sm font-medium">{form.name}</span>
									{#if isSuggested}
										<span
											class="bg-brand/10 text-brand inline-flex shrink-0 items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
										>
											<SparklesIcon class="size-2.5" /> Suggested
										</span>
									{/if}
								</span>
								<span class="text-muted-foreground block truncate text-xs">
									<span>{humanise(form.entityType)}</span> · {form.fieldCount} fields{#if form.documentType}
										· {DOC_TYPE_META[form.documentType]?.label}{/if}
								</span>
							</span>
							<StatusBadge tone={fm.tone}>{fm.label}</StatusBadge>
							{#if selected === form.code}<CheckIcon class="text-primary size-4 shrink-0" />{/if}
						</button>
					{/each}
				</div>

				{#if filtered.length === 0}
					<div
						class="text-muted-foreground flex flex-col items-center justify-center gap-2 py-12 text-center text-sm"
					>
						<SearchIcon class="size-8 opacity-40" />
						No forms match your filters.
					</div>
				{/if}
			{/if}
		</div>

		<!-- Footer -->
		<div class="flex items-center justify-between border-t px-6 py-4">
			<span class="text-muted-foreground text-xs">{filtered.length} of {forms.length} shown</span>
			<div class="flex items-center gap-2">
				<Button variant="ghost" onclick={() => (open = false)}>Cancel</Button>
				<Button onclick={save}>Save</Button>
			</div>
		</div>
	</Dialog.Content>
</Dialog.Root>
