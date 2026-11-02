<script lang="ts">
	import { Button } from '../components/ui/button/index.js';
	import * as Sheet from '../components/ui/sheet/index.js';
	import { Label } from '../components/ui/label/index.js';
	import { Input } from '../components/ui/input/index.js';
	import { Textarea } from '../components/ui/textarea/index.js';
	import { Checkbox } from '../components/ui/checkbox/index.js';
	import { SelectMenu } from '../components/ui/select-menu/index.js';
	import { StatusBadge } from '../components/ui/status-badge/index.js';
	import {
		docTypeMeta,
		DATASET_FORMAT_META,
		DATASET_SCOPE_META,
		type Dataset,
		type DocumentType,
		type Renderer,
		type NewTemplateInput
	} from './types.js';
	import { slugifyCode } from './helpers.js';
	import FormPicker from '../forms/form-picker.svelte';
	import type { Form } from '../forms/types.js';

	import LayoutTemplateIcon from '@lucide/svelte/icons/layout-template';
	import CheckCircleIcon from '@lucide/svelte/icons/check-circle-2';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import AlertCircleIcon from '@lucide/svelte/icons/circle-alert';
	import TrendingUpIcon from '@lucide/svelte/icons/trending-up';
	import ClipboardCheckIcon from '@lucide/svelte/icons/clipboard-check';
	import CalculatorIcon from '@lucide/svelte/icons/calculator';
	import CalendarCheckIcon from '@lucide/svelte/icons/calendar-check';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import FileTextIcon from '@lucide/svelte/icons/file-text';

	let {
		open = $bindable(false),
		datasets = [],
		forms = [],
		existingCodes = [],
		oncreate
	}: {
		open?: boolean;
		datasets?: Dataset[];
		forms?: Form[];
		existingCodes?: string[];
		oncreate?: (input: NewTemplateInput) => Promise<{ ok: boolean; error?: string }> | void;
	} = $props();

	type Step = 1 | 2 | 3 | 4;
	const stepLabels: Record<Step, string> = { 1: 'Type', 2: 'Details', 3: 'Inputs', 4: 'Datasets' };

	// Curated presets offered when creating a template (each with an icon + hint). document_type is
	// open-ended elsewhere (uploads/reclassify register any tag), but template creation offers this
	// sensible fixed set. `satisfies` keeps the keys literal so typeOrder/TYPE_META lookups stay
	// defined under noUncheckedIndexedAccess.
	const TYPE_META = {
		illustration: { icon: TrendingUpIcon, hint: 'COBS 13 projection illustrations' },
		suitability: { icon: ClipboardCheckIcon, hint: 'Advice & recommendation letters' },
		valuation: { icon: CalculatorIcon, hint: 'Point-in-time portfolio valuations' },
		review: { icon: CalendarCheckIcon, hint: 'Periodic portfolio reviews' },
		factfind: { icon: ClipboardListIcon, hint: 'Client data capture records' },
		statement: { icon: FileTextIcon, hint: 'Account & transaction statements' }
	} satisfies Record<string, { icon: typeof LayoutTemplateIcon; hint: string }>;

	const typeOrder: (keyof typeof TYPE_META)[] = [
		'illustration',
		'suitability',
		'valuation',
		'review',
		'factfind',
		'statement'
	];

	let step = $state<Step>(1);
	let creating = $state(false);
	let createError = $state<string | null>(null);

	let documentType = $state<DocumentType | null>(null);
	let name = $state('');
	let code = $state('');
	let codeEdited = $state(false);
	let description = $state('');
	let renderer = $state<Renderer>('weasy');
	let selectedDatasets = $state<string[]>([]);
	let selectedFormCode = $state<string | null>(null);

	$effect(() => {
		if (!codeEdited) code = slugifyCode(name);
	});

	const codeExists = $derived(code.trim().length > 0 && existingCodes.includes(code.trim()));
	const canContinueStep1 = $derived(documentType !== null);
	const canContinueStep2 = $derived(
		name.trim().length > 0 && code.trim().length > 0 && !codeExists
	);
	const dtm = $derived(documentType ? docTypeMeta(documentType) : null);
	const selectedDatasetMeta = $derived(datasets.filter((d) => selectedDatasets.includes(d.code)));

	function toggleDataset(dsCode: string) {
		selectedDatasets = selectedDatasets.includes(dsCode)
			? selectedDatasets.filter((c) => c !== dsCode)
			: [...selectedDatasets, dsCode];
	}

	function reset() {
		step = 1;
		creating = false;
		createError = null;
		documentType = null;
		name = '';
		code = '';
		codeEdited = false;
		description = '';
		renderer = 'weasy';
		selectedDatasets = [];
		selectedFormCode = null;
	}

	function handleClose() {
		open = false;
		setTimeout(reset, 300);
	}

	async function handleCreate() {
		if (!documentType || !canContinueStep2 || creating) return;
		const dt = documentType;
		createError = null;
		creating = true;
		// Await the create so a failure keeps the wizard open (input intact) with the error inline,
		// instead of closing + wiping all four steps and only flashing a banner on the list page.
		const result = await oncreate?.({
			name: name.trim(),
			code: code.trim(),
			documentType: dt,
			renderer,
			description: description.trim() || undefined,
			linkedDatasets: selectedDatasets,
			inputFormCode: selectedFormCode ?? undefined
		});
		if (result && !result.ok) {
			createError = result.error ?? 'Could not create the template.';
			creating = false;
			return;
		}
		handleClose();
	}
</script>

<Sheet.Root
	bind:open
	onOpenChange={(v) => {
		if (!v) handleClose();
	}}
>
	<Sheet.Content class="flex flex-col gap-0">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title class="flex items-center gap-2">
				<LayoutTemplateIcon class="text-primary size-4" />
				New template
			</Sheet.Title>

			<div class="mt-3 flex items-center gap-0">
				{#each [1, 2, 3, 4] as s (s)}
					<div class="flex items-center gap-1.5">
						<div
							class={[
								'flex size-5 items-center justify-center rounded-full text-[10px] font-semibold transition-colors',
								step === s
									? 'bg-primary text-primary-foreground'
									: step > s
										? 'bg-primary/20 text-primary'
										: 'bg-muted text-muted-foreground'
							].join(' ')}
						>
							{#if step > s}
								<CheckCircleIcon class="size-3" />
							{:else}
								{s}
							{/if}
						</div>
						<span
							class={[
								'text-xs transition-colors',
								step === s ? 'text-foreground font-medium' : 'text-muted-foreground'
							].join(' ')}
						>
							{stepLabels[s as Step]}
						</span>
					</div>
					{#if s < 4}
						<div class="bg-border mx-2 h-px flex-1"></div>
					{/if}
				{/each}
			</div>
		</Sheet.Header>

		<div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
			{#if step === 1}
				<div class="space-y-4">
					<p class="text-muted-foreground text-sm">
						Choose the kind of document this template produces. It drives the inputs available when
						generating.
					</p>
					<div class="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
						{#each typeOrder as t (t)}
							{@const meta = docTypeMeta(t)}
							{@const Icon = TYPE_META[t].icon}
							{@const selected = documentType === t}
							<button
								type="button"
								class={[
									'flex items-start gap-3 rounded-xl border p-3 text-left transition-all',
									selected
										? 'border-ring bg-muted/40 ring-ring/30 ring-1'
										: 'hover:border-ring/50 hover:bg-muted/20'
								].join(' ')}
								onclick={() => (documentType = t)}
							>
								<div
									class="flex size-9 shrink-0 items-center justify-center rounded-lg {meta.bgColor}"
								>
									<Icon class="size-4.5 {meta.textColor}" />
								</div>
								<div class="min-w-0">
									<p class="text-sm font-semibold">{meta.label}</p>
									<p class="text-muted-foreground mt-0.5 text-xs leading-snug">
										{TYPE_META[t].hint}
									</p>
								</div>
							</button>
						{/each}
					</div>
				</div>
			{:else if step === 2}
				<div class="space-y-4">
					<p class="text-muted-foreground text-sm">
						Name the template and give it a reference code. The code is used wherever the template
						is referenced programmatically.
					</p>
					<div class="space-y-1.5">
						<Label for="tpl-name">Name</Label>
						<Input id="tpl-name" placeholder="e.g. SIPP Illustration" bind:value={name} />
					</div>
					<div class="space-y-1.5">
						<Label for="tpl-code">Reference code</Label>
						<Input
							id="tpl-code"
							class="font-mono text-sm"
							placeholder="sipp_illustration"
							bind:value={code}
							oninput={() => (codeEdited = true)}
							aria-invalid={codeExists}
						/>
						{#if codeExists}
							<p class="text-destructive flex items-center gap-1 text-xs">
								<AlertCircleIcon class="size-3.5" />
								A template with this code already exists.
							</p>
						{:else}
							<p class="text-muted-foreground text-xs">
								Lower-case, words separated by underscores. Auto-filled from the name.
							</p>
						{/if}
					</div>
					<div class="space-y-1.5">
						<Label for="tpl-description">
							Description <span class="text-muted-foreground font-normal">(optional)</span>
						</Label>
						<Textarea
							id="tpl-description"
							rows={3}
							placeholder="What this template is for, and any compliance notes…"
							bind:value={description}
						/>
					</div>
					<div class="space-y-1.5">
						<Label for="tpl-renderer">Renderer</Label>
						<SelectMenu
							id="tpl-renderer"
							class="w-full"
							items={[
								{ value: 'weasy', label: 'WeasyPrint — HTML/CSS to PDF' },
								{ value: 'chromium', label: 'Chromium — headless browser print' }
							]}
							value={renderer}
							onValueChange={(v) => (renderer = v as Renderer)}
						/>
						<p class="text-muted-foreground text-xs">
							WeasyPrint suits letters and reports; Chromium for layouts needing full browser CSS.
						</p>
					</div>
				</div>
			{:else if step === 3}
				<div class="space-y-4">
					<p class="text-muted-foreground text-sm">
						Choose the form advisers fill in when generating. Suggested forms match this document
						type — pick any, or none.
					</p>
					<FormPicker
						{forms}
						documentType={documentType ?? undefined}
						bind:value={selectedFormCode}
					/>
				</div>
			{:else if step === 4}
				<div class="space-y-4">
					<p class="text-muted-foreground text-sm">
						Link the datasets this template draws on. You can change these later.
						<span class="text-muted-foreground/70">(optional)</span>
					</p>
					<div class="divide-y rounded-xl border">
						{#each datasets as ds (ds.uid)}
							{@const sm = DATASET_FORMAT_META[ds.format]}
							{@const scope = DATASET_SCOPE_META[ds.scope]}
							{@const checked = selectedDatasets.includes(ds.code)}
							<div
								class={[
									'flex items-center gap-3 px-4 py-3 transition-colors',
									ds.enabled ? 'hover:bg-muted/20 cursor-pointer' : 'cursor-not-allowed opacity-50'
								].join(' ')}
								role="button"
								title={ds.enabled
									? undefined
									: 'Disabled — enable this dataset under Datasets to link it here.'}
								tabindex={ds.enabled ? 0 : -1}
								onclick={() => ds.enabled && toggleDataset(ds.code)}
								onkeydown={(e) => {
									if (ds.enabled && (e.key === 'Enter' || e.key === ' ')) {
										e.preventDefault();
										toggleDataset(ds.code);
									}
								}}
							>
								<Checkbox
									{checked}
									disabled={!ds.enabled}
									aria-label={ds.name}
									tabindex={-1}
									class="pointer-events-none"
								/>
								<div class="min-w-0 flex-1">
									<p class="truncate text-sm font-medium">{ds.name}</p>
									<p class="text-muted-foreground truncate text-xs">{ds.code}</p>
								</div>
								<div class="flex shrink-0 items-center gap-2">
									<span
										class="rounded-full px-2 py-0.5 text-xs font-medium {sm.bgColor} {sm.textColor}"
									>
										{sm.label}
									</span>
									<span class="text-muted-foreground hidden text-xs sm:inline">{scope.label}</span>
								</div>
							</div>
						{:else}
							<p class="text-muted-foreground px-4 py-6 text-center text-sm">
								No datasets available to link — you can add them later from the template's page.
							</p>
						{/each}
					</div>

					<div class="bg-muted/20 rounded-xl border p-4">
						<p class="text-muted-foreground mb-3 text-xs font-semibold">Summary</p>
						<dl class="space-y-2">
							<div class="flex items-center justify-between">
								<dt class="text-muted-foreground text-sm">Name</dt>
								<dd class="text-sm font-medium">{name.trim() || '—'}</dd>
							</div>
							<div class="flex items-center justify-between">
								<dt class="text-muted-foreground text-sm">Code</dt>
								<dd>
									<code class="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">{code || '—'}</code
									>
								</dd>
							</div>
							<div class="flex items-center justify-between">
								<dt class="text-muted-foreground text-sm">Type</dt>
								<dd>
									{#if dtm}
										<span
											class="rounded-full px-2 py-0.5 text-xs font-medium {dtm.bgColor} {dtm.textColor}"
										>
											{dtm.label}
										</span>
									{:else}
										<span class="text-sm">—</span>
									{/if}
								</dd>
							</div>
							<div class="flex items-center justify-between">
								<dt class="text-muted-foreground text-sm">Renderer</dt>
								<dd class="text-sm font-medium">
									{renderer === 'weasy' ? 'WeasyPrint' : 'Chromium'}
								</dd>
							</div>
							<div class="flex items-center justify-between">
								<dt class="text-muted-foreground text-sm">Datasets</dt>
								<dd class="text-sm font-medium">{selectedDatasetMeta.length || 'None'}</dd>
							</div>
						</dl>
					</div>

					<div class="border-warning/20 bg-warning/5 flex items-start gap-2 rounded-lg border p-3">
						<DatabaseIcon class="text-warning mt-0.5 size-4 shrink-0" />
						<p class="text-muted-foreground text-xs">
							The template will be created as a <StatusBadge tone="neutral">Draft</StatusBadge> (v1).
							Open it in the editor to design the layout, then publish to make it available for generation.
						</p>
					</div>
				</div>
			{/if}
		</div>

		{#if createError}
			<p class="text-destructive px-6 pt-1 text-sm" role="alert">{createError}</p>
		{/if}

		<div class="flex items-center justify-between border-t px-6 py-4">
			<Button variant="ghost" onclick={handleClose}>Cancel</Button>
			<div class="flex items-center gap-2">
				{#if step > 1 && !creating}
					<Button variant="outline" onclick={() => (step = (step - 1) as Step)}>
						<ChevronLeftIcon class="mr-1 size-4" />
						Back
					</Button>
				{/if}
				{#if step < 4}
					<Button
						disabled={(step === 1 && !canContinueStep1) || (step === 2 && !canContinueStep2)}
						onclick={() => (step = (step + 1) as Step)}
					>
						Continue
						<ChevronRightIcon class="ml-1 size-4" />
					</Button>
				{:else if creating}
					<Button disabled>
						<LoaderCircleIcon class="mr-1.5 size-4 animate-spin" />
						Creating…
					</Button>
				{:else}
					<Button disabled={!canContinueStep2} onclick={handleCreate}>
						<LayoutTemplateIcon class="mr-1.5 size-4" />
						Create template
					</Button>
				{/if}
			</div>
		</div>
	</Sheet.Content>
</Sheet.Root>
