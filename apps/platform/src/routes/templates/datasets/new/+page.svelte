<script lang="ts">
	import { untrack } from 'svelte';
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Input,
		Label,
		PageHeader,
		SelectMenu,
		Textarea
	} from '@graphene/ui';
	import { enhance } from '$app/forms';
	import BackButton from '$lib/components/back-button.svelte';
	import { DatasetDataGrid } from '@graphene/ui/dataset-authoring';
	import DatasetParams from '$lib/components/datasets/dataset-params.svelte';
	import { toCsvLine } from '$lib/csv';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const v = $derived(form?.values);

	// A firm-wide dataset is resolved across the whole firm at generate, so the API only lets an
	// author with 'contacts:read_all' set one up.
	const canFirmWide = $derived(data.canFirmWide ?? false);
	// Only the entries this author may actually set up: a firm-wide one needs 'contacts:read_all',
	// so offering it without would earn a 403 on submit. The reason is stated below the picker.
	const entries = $derived(
		(data.catalogue ?? []).filter((e) => e.scope === 'subject' || canFirmWide)
	);

	// Defaults only — the form uses enhance, so these selections (and the grid below) persist across
	// a validation error without a remount; the text fields repopulate from `v` (the echoed values).
	let format = $state('inline');
	let catalogueKey = $state(untrack(() => v?.catalogue_key ?? ''));
	let paramValues = $state<Record<string, string>>({});
	let saving = $state(false);

	const formatItems = [
		{ value: 'inline', label: 'A table you type in' },
		{ value: 'catalogue', label: 'A dataset we provide' }
	];
	const entryItems = $derived(entries.map((e) => ({ value: e.key, label: e.name })));
	const chosen = $derived(entries.find((e) => e.key === catalogueKey) ?? null);
	/**
	 * SCOPE IS NOT A CONTROL. A provided entry declares whether it is per-client beside the SQL
	 * that binds (or does not bind) the client, and the API refuses a row that disagrees — so this
	 * follows the chosen entry, and a typed table is always firm-wide because the same rows go to
	 * everybody.
	 */
	const scope = $derived(format === 'catalogue' ? (chosen?.scope ?? 'subject') : 'global');

	// Re-seed the settings from the entry's own defaults whenever a different entry is picked: the
	// previous entry's settings are not the new one's, and carrying them over would post names it
	// does not declare (which the API refuses, rightly).
	$effect(() => {
		const entry = chosen;
		paramValues = entry
			? Object.fromEntries(entry.params.map((p) => [p.name, String(p.default ?? '')]))
			: {};
	});

	// Inline data — an editable grid (DatasetDataGrid), serialised (quote-aware) into the existing
	// columns/rows form contract via hidden inputs, so the server action is unchanged.
	let columns = $state<string[]>(['Column 1', 'Column 2']);
	let rows = $state<string[][]>([['', '']]);
	const filledRows = $derived(rows.filter((r) => r.some((c) => c.trim() !== '')));
	const columnsSerialized = $derived(toCsvLine(columns));
	const rowsSerialized = $derived(filledRows.map(toCsvLine).join('\n'));
</script>

<svelte:head><title>New dataset · Platform</title></svelte:head>

<div class="mx-auto max-w-2xl">
	<BackButton fallback="/templates/datasets" fallbackLabel="datasets" class="mb-4" />

	<PageHeader
		title="New dataset"
		description="A data source templates can draw on at render time."
	/>

	<form
		method="POST"
		use:enhance={() => {
			saving = true;
			return async ({ update }) => {
				await update();
				saving = false;
			};
		}}
		class="space-y-5"
	>
		{#if form?.error}
			<Alert variant="destructive">
				<AlertDescription>{form.error}</AlertDescription>
			</Alert>
		{/if}

		<div class="grid gap-4 sm:grid-cols-2">
			<div class="space-y-1.5">
				<Label for="name">Name</Label>
				<Input id="name" name="name" required value={v?.name ?? ''} placeholder="Client Holdings" />
			</div>
			<div class="space-y-1.5">
				<Label for="code">Code</Label>
				<Input id="code" name="code" required value={v?.code ?? ''} placeholder="client_holdings" />
				<p class="text-muted-foreground text-xs">Lower_snake_case. Used in templates.</p>
			</div>
		</div>

		<div class="space-y-1.5">
			<Label for="description"
				>Description <span class="text-muted-foreground">(optional)</span></Label
			>
			<Textarea id="description" name="description" rows={2} value={v?.description ?? ''} />
		</div>

		<div class="grid gap-4 sm:grid-cols-2">
			<div class="space-y-1.5">
				<Label for="format">Where the rows come from</Label>
				<SelectMenu id="format" name="format" items={formatItems} bind:value={format} />
			</div>
			<div class="space-y-1.5">
				<Label>Applies to</Label>
				<input type="hidden" name="scope" value={scope} />
				<p class="text-sm">
					{scope === 'subject' ? 'One client at a time' : 'The whole firm'}
				</p>
				<p class="text-muted-foreground text-xs">
					{#if format === 'catalogue'}
						Set by the dataset itself — a per-client one is filtered to the document's client.
					{:else}
						A table you type in is the same rows for every client.
					{/if}
				</p>
			</div>
		</div>

		{#if format === 'catalogue'}
			<div class="space-y-1.5">
				<Label for="catalogue_key">Dataset</Label>
				{#if data.catalogueError}
					<p class="text-destructive text-sm">
						The list of provided datasets couldn't be loaded. Reload the page to try again.
					</p>
				{:else if entryItems.length === 0}
					<p class="text-muted-foreground text-sm">
						There are no provided datasets available to you. A firm-wide one needs the “Read all
						clients” permission as well as dataset authoring.
					</p>
				{:else}
					<SelectMenu
						id="catalogue_key"
						name="catalogue_key"
						items={entryItems}
						bind:value={catalogueKey}
						placeholder="Choose a dataset…"
					/>
				{/if}
				{#if chosen}
					<p class="text-muted-foreground text-xs">{chosen.description}</p>
					{#if chosen.note}
						<p class="text-muted-foreground text-xs">{chosen.note}</p>
					{/if}
				{/if}
				{#if !canFirmWide && !data.catalogueError}
					<p class="text-muted-foreground text-xs">
						Firm-wide datasets aren't available to you: they run across every client in the firm
						when a document is generated, which needs the “Read all clients” permission as well as
						dataset authoring.
					</p>
				{/if}
			</div>

			{#if chosen && chosen.params.length > 0}
				<div class="space-y-3 rounded-lg border p-4">
					<p class="text-sm font-medium">Settings</p>
					<DatasetParams params={chosen.params} bind:values={paramValues} />
				</div>
			{/if}

			{#if chosen}
				<div class="space-y-1.5">
					<Label>Columns</Label>
					<div class="flex flex-wrap gap-1.5">
						{#each chosen.columns as c (c.name)}
							<Badge variant="outline" class="font-mono text-xs">{c.name}</Badge>
						{/each}
					</div>
					<p class="text-muted-foreground text-xs">
						What a template can put in a table or a chart from this dataset.
					</p>
				</div>
			{/if}
		{:else}
			<!-- Editable data grid — type into cells, add/remove rows & columns, or import a CSV. -->
			<DatasetDataGrid bind:columns bind:rows />
			<!-- Hidden fields: the server action parses these exactly as before (quote-aware). -->
			<input type="hidden" name="columns" value={columnsSerialized} />
			<input type="hidden" name="rows" value={rowsSerialized} />
		{/if}

		<div class="flex items-center justify-end gap-2 border-t pt-4">
			<Button type="button" variant="ghost" href="/templates/datasets">Cancel</Button>
			<Button type="submit" disabled={saving}>
				{saving ? 'Creating…' : 'Create dataset'}
			</Button>
		</div>
	</form>
</div>
