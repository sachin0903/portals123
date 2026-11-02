<script lang="ts">
	// One managed vocabulary (document types OR supporting-file types) — the add form + the
	// edit-inline / Save-changes list. Rendered twice by the page, once per `kind`, each with its own
	// draft state. Both post to the shared ?/create and ?/saveAll actions; create carries the kind.
	import { enhance } from '$app/forms';
	import { Button, Card, CardContent, Input, Switch } from '@graphene/ui';
	import { DataTable, StatusBadge, type DataTableColumn, type DataTableSort } from '@graphene/ui';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import type { DocumentType } from './+page.server';

	let {
		kind,
		title,
		description,
		types,
		labelPlaceholder,
		slugPlaceholder,
		emptyText,
		canManage
	}: {
		kind: 'document' | 'file';
		title: string;
		description: string;
		types: DocumentType[];
		labelPlaceholder: string;
		slugPlaceholder: string;
		emptyText: string;
		/** document_types:write — without it this renders read-only (no add form, no inline
		 *  editing, no Save changes): the server actions already refuse these without it, so a
		 *  visible-but-broken control would be a trap, not a convenience. */
		canManage: boolean;
	} = $props();

	let newSlug = $state('');
	let newLabel = $state('');

	// Editable copy, reseeded from the server whenever `types` changes (after a create/save), so the
	// switches + labels reflect what's persisted; local edits live here until Save changes.
	// `uid` is the row's address — `/documents/types/{uid}` is what an edit PATCHes. The slug is
	// the firm's own word for the type and can be renamed; the id never leaves the database.
	type Draft = { id: number; uid: string; slug: string; label: string; archived: boolean };
	let rows = $state<Draft[]>([]);
	$effect(() => {
		rows = types.map((t) => ({
			id: t.id,
			uid: t.uid,
			slug: t.slug,
			label: t.label,
			archived: t.archived
		}));
	});

	const changeCount = $derived(
		rows.filter((r) => {
			const orig = types.find((t) => t.id === r.id);
			return orig && (orig.label !== r.label.trim() || orig.archived !== r.archived);
		}).length
	);
	let saving = $state(false);
	let open = $state(true);
	let sort = $state<DataTableSort | null>(null);
	// Sort on what is saved, not the draft: a row must not move while its label is being typed.
	const saved = $derived(new Map(types.map((t) => [t.id, t])));

	const width = (w: string) => ({ headerClass: w, cellClass: w });
	// Centred only while it renders the Switch (canManage); view-only renders a pill, which reads left.
	const columns = $derived<DataTableColumn<Draft>[]>([
		{
			key: 'label',
			header: 'Label',
			sortable: true,
			sortDefault: true,
			sortValue: (row) => saved.get(row.id)?.label ?? row.label,
			...width('w-64')
		},
		{ key: 'slug', header: 'Slug', sortable: true, ...width('w-40') },
		{
			key: 'enabled',
			header: 'Enabled',
			align: canManage ? 'center' : undefined,
			sortable: true,
			sortValue: (row) => Number(!(saved.get(row.id)?.archived ?? row.archived)),
			...width('w-28')
		}
	]);
</script>

<!-- Each row's uid/label/archived inputs stay in DOM order together, so `saveAll`'s `getAll` zip
     still pairs them after a sort. -->
{#snippet table()}
	<DataTable
		{columns}
		{rows}
		getKey={(row) => row.id}
		bind:sort
		empty={emptyText}
		label={title}
		rowClass={(row) => (row.archived ? 'opacity-60' : undefined)}
	>
		{#snippet cell(row, column)}
			{#if column.key === 'label'}
				{#if canManage}
					<input type="hidden" name="uid" value={row.uid} />
					<Input
						name="label"
						bind:value={row.label}
						class="h-8 w-full"
						aria-label={`Rename ${row.slug}`}
					/>
				{:else}
					{row.label}
				{/if}
			{:else if column.key === 'slug'}
				<code class="text-muted-foreground bg-muted rounded px-1.5 py-0.5 font-mono text-xs"
					>{row.slug}</code
				>
			{:else if column.key === 'enabled' && canManage}
				<input type="hidden" name="archived" value={String(row.archived)} />
				<Switch
					checked={!row.archived}
					onCheckedChange={(v) => (row.archived = !v)}
					aria-label={`${row.archived ? 'Enable' : 'Disable'} ${row.slug}`}
				/>
			{:else if column.key === 'enabled'}
				<StatusBadge tone={row.archived ? 'neutral' : 'success'}>
					{row.archived ? 'Disabled' : 'Enabled'}
				</StatusBadge>
			{/if}
		{/snippet}
	</DataTable>
{/snippet}

<section class="space-y-3">
	<!-- Collapsible section header — click to fold the whole vocabulary away. -->
	<button
		type="button"
		onclick={() => (open = !open)}
		class="flex w-full items-center gap-2 text-left"
		aria-expanded={open}
	>
		<ChevronDownIcon
			class="text-muted-foreground size-4 shrink-0 transition-transform {open ? '' : '-rotate-90'}"
		/>
		<span class="min-w-0">
			<span class="block text-lg font-semibold"
				>{title} <span class="text-muted-foreground font-normal">({types.length})</span></span
			>
			<span class="text-muted-foreground block text-sm">{description}</span>
		</span>
	</button>

	{#if open}
		{#if canManage}
			<!-- Add a type (the hidden `kind` routes it to this vocabulary). -->
			<Card>
				<CardContent>
					<form
						method="POST"
						action="?/create"
						use:enhance={() =>
							async ({ update, result }) => {
								await update();
								if (result.type === 'success') {
									newSlug = '';
									newLabel = '';
								}
							}}
						class="flex flex-wrap items-end gap-2"
					>
						<input type="hidden" name="kind" value={kind} />
						<div class="space-y-1">
							<span class="text-muted-foreground text-xs">Label</span>
							<Input
								name="label"
								bind:value={newLabel}
								placeholder={labelPlaceholder}
								class="w-64"
							/>
						</div>
						<div class="space-y-1">
							<span class="text-muted-foreground text-xs">Slug (stable key)</span>
							<Input name="slug" bind:value={newSlug} placeholder={slugPlaceholder} class="w-56" />
						</div>
						<Button type="submit" disabled={!newSlug.trim() || !newLabel.trim()}>Add type</Button>
					</form>
				</CardContent>
			</Card>

			<!-- Existing types: edit labels + flip Enabled inline, then commit the lot with Save changes. -->
			<form
				method="POST"
				action="?/saveAll"
				use:enhance={() => {
					saving = true;
					return async ({ update }) => {
						await update();
						saving = false;
					};
				}}
			>
				<Card>
					<CardContent>
						{@render table()}

						<div class="mt-3 flex items-center justify-end gap-3">
							{#if changeCount > 0}
								<span class="text-muted-foreground text-sm">
									{changeCount} unsaved change{changeCount === 1 ? '' : 's'}
								</span>
							{/if}
							<Button type="submit" disabled={changeCount === 0 || saving}>
								{saving ? 'Saving…' : 'Save changes'}
							</Button>
						</div>
					</CardContent>
				</Card>
			</form>
		{:else}
			<!-- View-only (document_types:read without :write): the same rows, no inputs, no forms. -->
			<Card>
				<CardContent>
					{@render table()}
				</CardContent>
			</Card>
		{/if}
	{/if}
</section>
