<script lang="ts">
	// Change the account's investment mandate — same drawer family as Payment / Withdraw.
	//
	// It states the model the account is on before it offers another, and once another is chosen
	// it says in a sentence what the save will do with both names. A picker on its own asked the
	// adviser to remember the current mandate and to trust that "Change model" meant what they
	// thought it meant.
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import { untrack } from 'svelte';
	import { Button } from '../../ui/button/index.js';
	import { Label } from '../../ui/label/index.js';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import * as Sheet from '../../ui/sheet/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import type { CatalogModel } from '../clients/types.js';
	import { modelChangeAction, modelChangeSummary, modelFacts, NO_MODEL } from './model-change.js';

	let {
		open = $bindable(false),
		models = [],
		modelId = null,
		modelName = null,
		justificationRequired = false,
		submit
	}: {
		open: boolean;
		models?: CatalogModel[];
		modelId?: number | null;
		modelName?: string | null;
		/** The firm asks for a reason before the mandate changes. */
		justificationRequired?: boolean;
		submit: (body: { model_id: number | null; justification?: string }) => Promise<string | null>;
	} = $props();

	let draft = $state('');
	let justification = $state('');
	let saving = $state(false);
	let error = $state<string | null>(null);

	const items = $derived([
		{ value: '', label: NO_MODEL },
		...models.map((model) => ({ value: String(model.id), label: model.name })),
		...(modelId != null && !models.some((model) => model.id === modelId)
			? [{ value: String(modelId), label: modelName ?? `Model ${modelId}` }]
			: [])
	]);
	const current = $derived(modelId == null ? '' : String(modelId));
	const changed = $derived(draft !== current);
	const ready = $derived(
		changed && !saving && (!justificationRequired || Boolean(justification.trim()))
	);

	const currentModel = $derived(models.find((model) => model.id === modelId) ?? null);
	const currentName = $derived(
		modelId == null ? null : (currentModel?.name ?? modelName ?? `Model ${modelId}`)
	);
	const nextModel = $derived(
		draft === '' ? null : (models.find((model) => String(model.id) === draft) ?? null)
	);
	const nextName = $derived(
		draft === '' ? null : (nextModel?.name ?? items.find((i) => i.value === draft)?.label ?? null)
	);
	const summary = $derived(modelChangeSummary(currentName, nextName, changed));
	const action = $derived(changed ? modelChangeAction(currentName, nextName) : 'Change model');
	const currentFacts = $derived(modelFacts(currentModel));
	const nextFacts = $derived(modelFacts(nextModel));

	function reset() {
		draft = current;
		justification = '';
		error = null;
		saving = false;
	}

	// The host opens this drawer by setting `open`, which does not fire `onOpenChange`, so the
	// draft has to be seeded here. Without it the picker opened on '' while the account had a
	// model, and the button was armed to clear a mandate nobody had asked to clear.
	$effect(() => {
		if (open) untrack(reset);
	});

	async function save() {
		if (!changed) {
			error = 'Choose a different model, or clear the mandate.';
			return;
		}
		if (justificationRequired && !justification.trim()) {
			error = 'Say why this mandate is changing.';
			return;
		}
		saving = true;
		error = null;
		const next = draft === '' ? null : Number(draft);
		const refusal = await submit({
			model_id: next,
			...(justificationRequired && justification.trim()
				? { justification: justification.trim() }
				: {})
		});
		saving = false;
		if (refusal) {
			error = refusal;
			return;
		}
		open = false;
		reset();
	}
</script>

{#snippet facts(rows: [string, string][])}
	{#if rows.length}
		<dl
			class="text-muted-foreground mt-1 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs"
		>
			{#each rows as [label, value] (label)}
				<dt>{label}</dt>
				<dd class="text-foreground tabular-nums">{value}</dd>
			{/each}
		</dl>
	{/if}
{/snippet}

<Sheet.Root
	{open}
	onOpenChange={(next) => {
		open = next;
		if (next) reset();
		else {
			justification = '';
			error = null;
			saving = false;
		}
	}}
>
	<Sheet.Content side="right" class="gap-0 p-0 sm:max-w-xl">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title>Change model</Sheet.Title>
			<Sheet.Description>
				The mandate this account is run to. Clearing it leaves the account without a model.
			</Sheet.Description>
		</Sheet.Header>

		<div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">
			<!-- The model the account is on now, stated first so the picker below reads as
			     "instead of this". -->
			<div class="bg-muted/40 rounded-lg border px-3 py-2.5" data-testid="current-model">
				<p class="text-muted-foreground text-xs">Current model</p>
				<p class="text-sm font-medium">{currentName ?? NO_MODEL}</p>
				{@render facts(currentFacts)}
			</div>

			<div class="space-y-1.5">
				<Label for="account-model">New model</Label>
				<SelectMenu
					id="account-model"
					{items}
					bind:value={draft}
					placeholder="Choose a model"
					portal={false}
				/>
				{#if !changed}
					<p class="text-muted-foreground text-xs">
						Choose a different model, or No model to clear the mandate.
					</p>
				{/if}
			</div>

			{#if summary}
				<!-- Both names, before the button is pressed. This is what the reviewer will read
				     on the hold too, so the adviser and the approver see the same change. -->
				<div
					class="border-brand/40 bg-brand/5 rounded-lg border px-3 py-2.5"
					data-testid="model-change"
				>
					<div class="flex flex-wrap items-center gap-2 text-sm">
						<span class="text-muted-foreground line-through">{currentName ?? NO_MODEL}</span>
						<ArrowRightIcon class="text-muted-foreground size-4 shrink-0" />
						<span class="font-medium">{nextName ?? NO_MODEL}</span>
					</div>
					<p class="text-muted-foreground mt-1 text-xs">{summary}</p>
					{@render facts(nextFacts)}
				</div>
			{/if}

			{#if justificationRequired}
				<div class="space-y-1.5">
					<Label for="account-model-justification">Why this change</Label>
					<Textarea id="account-model-justification" bind:value={justification} rows={2} />
					<p class="text-muted-foreground text-xs">
						A second person must approve before this mandate changes.
					</p>
				</div>
			{/if}

			{#if error}
				<p class="text-destructive text-sm">{error}</p>
			{/if}
		</div>

		<Sheet.Footer class="border-t">
			<Button onclick={save} disabled={!ready}>
				{saving ? 'Saving…' : action}
			</Button>
			<Button variant="ghost" onclick={() => (open = false)}>Cancel</Button>
		</Sheet.Footer>
	</Sheet.Content>
</Sheet.Root>
