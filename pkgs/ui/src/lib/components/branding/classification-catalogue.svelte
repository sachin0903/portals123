<script lang="ts">
	import { Button } from '../ui/button/index.js';
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import { Switch } from '../ui/switch/index.js';
	import MultiSelect from '../ui/multi-select/multi-select.svelte';
	import SelectMenu from '../ui/select-menu/select-menu.svelte';
	import * as Card from '../ui/card/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import { Badge } from '../ui/badge/index.js';
	import {
		draftFrom,
		emptyDraft,
		matchSummary,
		type ClassificationDraft,
		type ClientClassification
	} from './classifications.js';

	let {
		classifications = [],
		designs = [],
		canManage = false,
		canAssignDesign = false,
		saving = false,
		onSave,
		onDelete,
		onAssignDesign
	}: {
		classifications?: ClientClassification[];
		designs?: { id: string; name: string; classification_slugs?: string[] }[];
		canManage?: boolean;
		canAssignDesign?: boolean;
		saving?: boolean;
		onSave?: (draft: ClassificationDraft) => Promise<void> | void;
		onDelete?: (uid: string) => Promise<void> | void;
		onAssignDesign?: (classificationSlug: string, designId: string | null) => void;
	} = $props();

	const SERVICE_OPTIONS = [
		{ value: 'advised', label: 'Advised' },
		{ value: 'execution_only', label: 'Execution only' },
		{ value: 'discretionary', label: 'Discretionary' }
	];
	const TYPE_OPTIONS = [
		{ value: 'individual', label: 'Individual' },
		{ value: 'organisation', label: 'Organisation' }
	];

	let open = $state(false);
	let draft = $state<ClassificationDraft>(emptyDraft());
	let error = $state<string | null>(null);
	let pending = $state(false);
	const designOptions = $derived([
		{ value: '__default', label: 'Default design' },
		...designs.map((design) => ({ value: design.id, label: design.name || design.id }))
	]);

	function assignedDesign(slug: string): string {
		return designs.find((design) => design.classification_slugs?.includes(slug))?.id ?? '__default';
	}

	function openNew() {
		draft = emptyDraft((classifications.at(-1)?.sort_order ?? -10) + 10);
		error = null;
		open = true;
	}

	function openEdit(row: ClientClassification) {
		draft = draftFrom(row);
		error = null;
		open = true;
	}

	async function save() {
		if (!onSave) return;
		if (!draft.name.trim()) {
			error = 'Name a book.';
			return;
		}
		pending = true;
		error = null;
		try {
			await onSave(draft);
			open = false;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Could not save that book.';
		} finally {
			pending = false;
		}
	}

	async function remove(uid: string) {
		if (!onDelete) return;
		pending = true;
		try {
			await onDelete(uid);
		} finally {
			pending = false;
		}
	}
</script>

<Card.Root>
	<Card.Header>
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div class="space-y-1.5">
				<Card.Title>Client books</Card.Title>
				<Card.Description>
					Match clients into a book, choose its portal design and control self-service money. A pin
					on the client record overrides the rules.
				</Card.Description>
			</div>
			{#if canManage}
				<Button size="sm" onclick={openNew}>New book</Button>
			{/if}
		</div>
	</Card.Header>
	<Card.Content class="space-y-3">
		{#if classifications.length === 0}
			<p class="text-muted-foreground text-sm">
				No books yet. Add <span class="text-foreground font-medium">Advised</span> (read-only) and
				<span class="text-foreground font-medium">D2C</span> (self-serve money) when both live in this
				firm.
			</p>
		{:else}
			<ul class="divide-border divide-y">
				{#each classifications as row (row.uid)}
					<li
						class="grid items-center gap-3 py-3 first:pt-0 last:pb-0 md:grid-cols-[minmax(0,1fr)_12rem_auto]"
					>
						<div class="min-w-0 space-y-1">
							<div class="flex flex-wrap items-center gap-2">
								<p class="text-sm font-medium">{row.name}</p>
								<code class="text-muted-foreground text-xs">{row.slug}</code>
								{#if row.is_default}
									<Badge variant="outline">Default</Badge>
								{/if}
								{#if row.capabilities.money_movement}
									<Badge variant="secondary">Can move money</Badge>
								{/if}
							</div>
							<p class="text-muted-foreground text-xs">{matchSummary(row.match)}</p>
						</div>
						<div class="space-y-1">
							<Label class="text-muted-foreground text-xs" for={`book-design-${row.uid}`}>
								Portal design
							</Label>
							<SelectMenu
								id={`book-design-${row.uid}`}
								size="sm"
								items={designOptions}
								value={assignedDesign(row.slug)}
								disabled={!canAssignDesign || designs.length === 0}
								onValueChange={(value) =>
									onAssignDesign?.(row.slug, value === '__default' ? null : value)}
							/>
						</div>
						{#if canManage}
							<div class="flex justify-end gap-1">
								<Button variant="ghost" size="sm" onclick={() => openEdit(row)}>Edit</Button>
								<Button
									variant="ghost"
									size="sm"
									disabled={pending || saving}
									onclick={() => remove(row.uid)}
								>
									Delete
								</Button>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</Card.Content>
</Card.Root>

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>{draft.uid ? 'Edit book' : 'New book'}</Dialog.Title>
			<Dialog.Description>
				Rules run in sort order. The first match wins; the default catches everyone else.
			</Dialog.Description>
		</Dialog.Header>
		<div class="grid gap-4">
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="clf-name">Name</Label>
					<Input id="clf-name" bind:value={draft.name} maxlength={40} />
				</div>
				<div class="space-y-1.5">
					<Label for="clf-slug">Slug</Label>
					<Input id="clf-slug" bind:value={draft.slug} placeholder="from the name" maxlength={32} />
				</div>
			</div>
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="clf-order">Sort order</Label>
					<Input id="clf-order" type="number" bind:value={draft.sort_order} />
				</div>
				<div class="flex items-center gap-3 pt-6">
					<Switch
						checked={draft.is_default}
						onCheckedChange={(checked) => (draft.is_default = checked)}
					/>
					<div>
						<p class="text-sm font-medium">Default</p>
						<p class="text-muted-foreground text-xs">Used when no rule matches.</p>
					</div>
				</div>
			</div>
			<div class="flex items-center gap-3">
				<Switch
					checked={Boolean(draft.capabilities.money_movement)}
					onCheckedChange={(checked) => (draft.capabilities = { money_movement: checked })}
				/>
				<div>
					<p class="text-sm font-medium">Clients can add and withdraw money</p>
					<p class="text-muted-foreground text-xs">Off for advised books. On for self-serve D2C.</p>
				</div>
			</div>
			<div class="space-y-1.5">
				<Label>Contact types</Label>
				<MultiSelect
					options={TYPE_OPTIONS}
					bind:value={
						() => draft.match.contact_types ?? [],
						(value) => (draft.match = { ...draft.match, contact_types: value })
					}
					placeholder="Any"
				/>
			</div>
			<div class="space-y-1.5">
				<Label>Any of these account services</Label>
				<MultiSelect
					options={SERVICE_OPTIONS}
					bind:value={
						() => draft.match.any_account_service_types ?? [],
						(value) => (draft.match = { ...draft.match, any_account_service_types: value })
					}
					placeholder="Any"
				/>
			</div>
			<div class="space-y-1.5">
				<Label>Every account must be</Label>
				<MultiSelect
					options={SERVICE_OPTIONS}
					bind:value={
						() => draft.match.account_service_types ?? [],
						(value) => (draft.match = { ...draft.match, account_service_types: value })
					}
					placeholder="No restriction"
				/>
			</div>
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="clf-min">Minimum AUM</Label>
					<Input
						id="clf-min"
						type="number"
						value={draft.match.min_value ?? ''}
						oninput={(event) => {
							const raw = event.currentTarget.value;
							draft.match = {
								...draft.match,
								min_value: raw === '' ? null : Number(raw)
							};
						}}
					/>
				</div>
				<div class="space-y-1.5">
					<Label for="clf-max">Maximum AUM</Label>
					<Input
						id="clf-max"
						type="number"
						value={draft.match.max_value ?? ''}
						oninput={(event) => {
							const raw = event.currentTarget.value;
							draft.match = {
								...draft.match,
								max_value: raw === '' ? null : Number(raw)
							};
						}}
					/>
				</div>
			</div>
			{#if error}
				<p class="text-destructive text-sm">{error}</p>
			{/if}
		</div>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (open = false)}>Cancel</Button>
			<Button onclick={save} disabled={pending || saving}>
				{pending ? 'Saving…' : 'Save book'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
