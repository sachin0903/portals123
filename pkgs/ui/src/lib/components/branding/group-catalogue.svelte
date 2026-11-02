<script lang="ts">
	import { Button } from '../ui/button/index.js';
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import SelectMenu from '../ui/select-menu/select-menu.svelte';
	import * as Card from '../ui/card/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import { emptyGroupDraft, groupDraftFrom, type ClientGroup, type GroupDraft } from './groups.js';

	let {
		groups = [],
		designs = [],
		canManage = false,
		canAssignDesign = false,
		saving = false,
		onSave,
		onDelete,
		onAssignDesign
	}: {
		groups?: ClientGroup[];
		designs?: { id: string; name: string; group_slugs?: string[] }[];
		canManage?: boolean;
		canAssignDesign?: boolean;
		saving?: boolean;
		onSave?: (draft: GroupDraft) => Promise<void> | void;
		onDelete?: (uid: string) => Promise<void> | void;
		onAssignDesign?: (groupSlug: string, designId: string | null) => void;
	} = $props();

	let open = $state(false);
	let draft = $state<GroupDraft>(emptyGroupDraft());
	let error = $state<string | null>(null);
	let pending = $state(false);
	const designOptions = $derived([
		{ value: '__default', label: 'Follow the book' },
		...designs.map((design) => ({ value: design.id, label: design.name || design.id }))
	]);

	function assignedDesign(slug: string): string {
		return designs.find((design) => design.group_slugs?.includes(slug))?.id ?? '__default';
	}

	function openNew() {
		draft = emptyGroupDraft((groups.at(-1)?.sort_order ?? -10) + 10);
		error = null;
		open = true;
	}

	function openEdit(row: ClientGroup) {
		draft = groupDraftFrom(row);
		error = null;
		open = true;
	}

	async function save() {
		if (!onSave) return;
		if (!draft.name.trim()) {
			error = 'Name a group.';
			return;
		}
		pending = true;
		error = null;
		try {
			await onSave(draft);
			open = false;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Could not save that group.';
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
				<Card.Title>Client groups</Card.Title>
				<Card.Description>
					Tag clients by business stream or proposition, and give a group its own portal design. A
					client can be in several. The first group in this list that has a design is the one they
					see. Their book still decides whether they can move money.
				</Card.Description>
			</div>
			{#if canManage}
				<Button size="sm" onclick={openNew}>New group</Button>
			{/if}
		</div>
	</Card.Header>
	<Card.Content class="space-y-3">
		{#if groups.length === 0}
			<p class="text-muted-foreground text-sm">
				No groups yet. Add one for each shopfront a stream should see — Workplace, Charity, a named
				proposition — then tag the clients who belong in it.
			</p>
		{:else}
			<ul class="divide-border divide-y">
				{#each groups as row (row.uid)}
					<li
						class="grid items-center gap-3 py-3 first:pt-0 last:pb-0 md:grid-cols-[minmax(0,1fr)_12rem_auto]"
					>
						<div class="min-w-0 space-y-1">
							<div class="flex flex-wrap items-center gap-2">
								<p class="text-sm font-medium">{row.name}</p>
								<code class="text-muted-foreground text-xs">{row.slug}</code>
							</div>
							{#if row.description}
								<p class="text-muted-foreground text-xs">{row.description}</p>
							{/if}
						</div>
						<div class="space-y-1">
							<Label class="text-muted-foreground text-xs" for={`group-design-${row.uid}`}>
								Portal design
							</Label>
							<SelectMenu
								id={`group-design-${row.uid}`}
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
			<Dialog.Title>{draft.uid ? 'Edit group' : 'New group'}</Dialog.Title>
			<Dialog.Description>
				Lower sort order wins when a client is in more than one group that has a design.
			</Dialog.Description>
		</Dialog.Header>
		<div class="grid gap-4">
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="grp-name">Name</Label>
					<Input id="grp-name" bind:value={draft.name} maxlength={40} />
				</div>
				<div class="space-y-1.5">
					<Label for="grp-slug">Slug</Label>
					<Input id="grp-slug" bind:value={draft.slug} placeholder="from the name" maxlength={32} />
				</div>
			</div>
			<div class="space-y-1.5">
				<Label for="grp-description">Description</Label>
				<Input id="grp-description" bind:value={draft.description} maxlength={160} />
			</div>
			<div class="space-y-1.5">
				<Label for="grp-order">Sort order</Label>
				<Input id="grp-order" type="number" bind:value={draft.sort_order} />
			</div>
			{#if error}<p class="text-destructive text-sm">{error}</p>{/if}
		</div>
		<Dialog.Footer>
			<Button variant="outline" onclick={() => (open = false)}>Cancel</Button>
			<Button onclick={save} disabled={pending || saving}>{pending ? 'Saving…' : 'Save'}</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
