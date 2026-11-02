<script lang="ts">
	import { untrack } from 'svelte';
	import { dndzone, type DndEvent } from 'svelte-dnd-action';
	import { flip } from 'svelte/animate';
	import type { BrandingPage } from '@graphene/auth';
	import GripVerticalIcon from '@lucide/svelte/icons/grip-vertical';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '../ui/button/index.js';
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import { Switch } from '../ui/switch/index.js';
	import { ConfirmDialog } from '../ui/confirm-dialog/index.js';
	import { PAGE_CAPS, PAGE_ICONS, PAGE_ICON_NAMES, slugify } from './portal-pages.js';
	import {
		addPage,
		removePage,
		renamePage,
		reorderPages,
		setPageIcon,
		setPageNav,
		setPageSlug
	} from './page-ops.js';
	import { navToggleBlock, slugErrorMessage } from './pages-view.js';
	import { cn } from '../../utils.js';

	// The Pages view's left rail: the working page set in order, with the selected page expanded
	// into its detail editor (rename / address / icon / menu toggle / delete). Every mutation goes
	// through page-ops — the rail holds NO bespoke page logic, it only chooses which op to call and
	// renders the op's refusals as human copy. The parent owns the value; `onchange` hands back the
	// whole next page list.
	let {
		pages,
		selectedId = null,
		canManage = true,
		onchange,
		onselect
	}: {
		pages: BrandingPage[];
		selectedId?: string | null;
		canManage?: boolean;
		onchange: (pages: BrandingPage[]) => void;
		onselect: (id: string) => void;
	} = $props();

	// Local dnd mirror, re-seeded on a genuinely new parent value (signature compare, like the
	// layout builder) so our own emits echoed back never clobber an in-flight drag.
	function signature(list: BrandingPage[]): string {
		return list
			.map((p) => `${p.id}:${p.label}:${p.slug}:${p.icon ?? ''}:${p.nav !== false}`)
			.join('|');
	}
	let items = $state<BrandingPage[]>(untrack(() => [...pages]));
	let lastSig = $state(untrack(() => signature(pages)));
	$effect(() => {
		const sig = signature(pages);
		if (sig !== lastSig) {
			lastSig = sig;
			items = [...pages];
		}
	});

	// Drag only starts from the grip handle (layout-builder's dragDisabled pattern), so clicking a
	// row to select it never accidentally drags.
	let dragDisabled = $state(true);
	function startDrag(e: PointerEvent) {
		if (!canManage) return;
		if (e.pointerType === 'mouse' && e.button !== 0) return;
		dragDisabled = false;
	}
	function handleConsider(e: CustomEvent<DndEvent<BrandingPage>>) {
		items = e.detail.items;
	}
	function handleFinalize(e: CustomEvent<DndEvent<BrandingPage>>) {
		items = e.detail.items;
		dragDisabled = true;
		const next = reorderPages(
			pages,
			items.map((p) => p.id)
		);
		lastSig = signature(next);
		onchange(next);
	}
	const dndOptions = $derived({
		items,
		flipDurationMs: 160,
		dragDisabled: !canManage || dragDisabled,
		dropTargetStyle: {}
	});

	const iconOf = (name: string | undefined) => PAGE_ICONS[name ?? ''] ?? PAGE_ICONS['layout-grid'];
	const hrefOf = (p: BrandingPage) => (p.slug === 'home' ? '/' : `/${p.slug}`);
	const selected = $derived(pages.find((p) => p.id === selectedId) ?? null);

	// ── Slug editing ────────────────────────────────────────────────────────
	// The draft is local so an invalid keystroke stays visible with its error instead of being
	// snapped back to the last valid slug; only a slug setPageSlug accepts is committed.
	let slugDraft = $state('');
	let slugError = $state<string | null>(null);
	let slugFor = $state<string | null>(null);
	$effect(() => {
		if (selectedId !== slugFor || (slugError === null && selected && selected.slug !== slugDraft)) {
			slugFor = selectedId;
			slugDraft = selected?.slug ?? '';
			slugError = null;
		}
	});
	function editSlug(value: string) {
		if (!selected) return;
		slugDraft = value;
		if (value === selected.slug) {
			slugError = null;
			return;
		}
		const result = setPageSlug(pages, selected.id, value);
		if ('error' in result) {
			slugError = slugErrorMessage(result.error);
		} else {
			slugError = null;
			onchange(result.pages);
		}
	}

	// ── Add page ────────────────────────────────────────────────────────────
	let newLabel = $state('');
	const atCap = $derived(pages.length >= PAGE_CAPS.maxPages);
	const newSlugPreview = $derived(newLabel.trim() ? slugify(newLabel) : null);
	function add(e: SubmitEvent) {
		e.preventDefault();
		const label = newLabel.trim();
		if (!label || atCap) return;
		const result = addPage(pages, label);
		if (!result) return; // cap raced; the button hint already explains
		newLabel = '';
		onchange(result.pages);
		onselect(result.id);
	}

	// ── Delete ──────────────────────────────────────────────────────────────
	let deleteOpen = $state(false);
</script>

<div class="space-y-3">
	<div
		class="space-y-1"
		use:dndzone={dndOptions}
		onconsider={handleConsider}
		onfinalize={handleFinalize}
	>
		{#each items as p (p.id)}
			{@const Icon = iconOf(p.icon)}
			<div animate:flip={{ duration: 160 }} class="rounded-md">
				<div
					class={cn(
						'flex items-center gap-1 rounded-md border px-1.5 py-1.5',
						selectedId === p.id
							? 'border-primary/60 bg-accent/60'
							: 'hover:bg-accent/40 border-transparent',
						p.nav === false && 'opacity-60'
					)}
				>
					{#if canManage}
						<button
							type="button"
							class="text-muted-foreground hover:text-foreground shrink-0 touch-none cursor-grab p-0.5 active:cursor-grabbing"
							aria-label={`Move ${p.label}`}
							onpointerdown={startDrag}
						>
							<GripVerticalIcon class="size-3.5" />
						</button>
					{/if}
					<button
						type="button"
						class="flex min-w-0 flex-1 items-center gap-2 text-left"
						aria-current={selectedId === p.id ? 'true' : undefined}
						onclick={() => onselect(p.id)}
					>
						<Icon class="text-muted-foreground size-4 shrink-0" />
						<span class="truncate text-sm font-medium">{p.label}</span>
						<span class="text-muted-foreground ml-auto shrink-0 font-mono text-[0.65rem]">
							{hrefOf(p)}
						</span>
					</button>
				</div>

				{#if selectedId === p.id && selected}
					{@const navBlocked = navToggleBlock(pages, p.id)}
					<!-- Selected-page detail. Lives inside the dnd item so it travels with the row. -->
					<div class="bg-muted/30 mt-1 space-y-3 rounded-md border p-2.5">
						<div class="space-y-1">
							<Label class="text-xs" for={`page-label-${p.id}`}>Name</Label>
							<Input
								id={`page-label-${p.id}`}
								class="h-8 text-sm"
								value={selected.label}
								disabled={!canManage}
								oninput={(e) => onchange(renamePage(pages, p.id, e.currentTarget.value))}
							/>
						</div>
						<div class="space-y-1">
							<Label class="text-xs" for={`page-slug-${p.id}`}>Address</Label>
							<div class="flex items-center gap-1">
								<span class="text-muted-foreground font-mono text-xs">/</span>
								<Input
									id={`page-slug-${p.id}`}
									class="h-8 font-mono text-xs"
									value={slugDraft}
									disabled={!canManage}
									aria-invalid={slugError !== null}
									oninput={(e) => editSlug(e.currentTarget.value)}
								/>
							</div>
							{#if slugError}
								<p class="text-destructive text-[0.7rem] leading-snug">{slugError}</p>
							{/if}
						</div>
						<div class="space-y-1">
							<Label class="text-xs">Icon</Label>
							<div class="grid grid-cols-7 gap-1">
								{#each PAGE_ICON_NAMES as name (name)}
									{@const OptionIcon = iconOf(name)}
									<button
										type="button"
										title={name}
										aria-label={`Icon: ${name}`}
										aria-pressed={(selected.icon ?? 'layout-grid') === name}
										disabled={!canManage}
										onclick={() => onchange(setPageIcon(pages, p.id, name))}
										class={cn(
											'flex size-7 items-center justify-center rounded-md border transition-colors disabled:opacity-50',
											(selected.icon ?? 'layout-grid') === name
												? 'border-primary bg-accent text-foreground'
												: 'text-muted-foreground hover:text-foreground hover:border-ring/50 border-transparent'
										)}
									>
										<OptionIcon class="size-4" />
									</button>
								{/each}
							</div>
						</div>
						<div class="space-y-1">
							<div class="flex items-center justify-between gap-2">
								<Label class="text-xs">Show in menu</Label>
								<Switch
									class="scale-90"
									checked={selected.nav !== false}
									disabled={!canManage || navBlocked !== null}
									aria-label={`Show ${selected.label} in the menu`}
									onCheckedChange={(v) => onchange(setPageNav(pages, p.id, v))}
								/>
							</div>
							{#if navBlocked}
								<!-- Inline reason rather than a hover tooltip: disabled controls swallow
								     pointer events, so a tooltip on one never opens reliably. -->
								<p class="text-muted-foreground text-[0.7rem] leading-snug">{navBlocked}</p>
							{/if}
						</div>
						{#if canManage}
							<Button
								variant="ghost"
								size="sm"
								class="text-destructive hover:text-destructive w-full gap-1.5"
								disabled={pages.length <= 1}
								onclick={() => (deleteOpen = true)}
							>
								<Trash2Icon class="size-3.5" />
								Delete page
							</Button>
							{#if pages.length <= 1}
								<p class="text-muted-foreground text-[0.7rem] leading-snug">
									The portal needs at least one page.
								</p>
							{/if}
						{/if}
					</div>
				{/if}
			</div>
		{/each}
	</div>

	{#if canManage}
		<form class="space-y-1 border-t pt-3" onsubmit={add}>
			<div class="flex items-center gap-1.5">
				<Input
					class="h-8 text-sm"
					placeholder="New page name"
					bind:value={newLabel}
					disabled={atCap}
					aria-label="New page name"
				/>
				<Button
					type="submit"
					variant="outline"
					size="sm"
					class="shrink-0 gap-1"
					disabled={atCap || !newLabel.trim()}
				>
					<PlusIcon class="size-3.5" />
					Add
				</Button>
			</div>
			{#if atCap}
				<p class="text-muted-foreground text-[0.7rem] leading-snug">
					The portal holds at most {PAGE_CAPS.maxPages} pages — delete one to add another.
				</p>
			{:else if newSlugPreview}
				<p class="text-muted-foreground font-mono text-[0.7rem]">/{newSlugPreview}</p>
			{/if}
		</form>
	{/if}
</div>

{#if selected}
	<ConfirmDialog
		bind:open={deleteOpen}
		title={`Delete page ‘${selected.label}’?`}
		description="Its layout is lost."
		confirmLabel="Delete page"
		destructive
		onconfirm={() => selected && onchange(removePage(pages, selected.id))}
	/>
{/if}
