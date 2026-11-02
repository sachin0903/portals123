<script module lang="ts">
	import type { FilterDef } from '../book/listing/filter-panel.svelte';

	/** A pure function so it can be pinned in a test without rendering the panel, which lives
	 *  behind a popover shut at SSR. `single`: armed/off is the whole vocabulary, no "or" to ask. */
	export function automationFilterDefs(): FilterDef[] {
		return [
			{
				key: 'enabled',
				label: 'Armed',
				single: true,
				allLabel: 'All statuses',
				options: [
					{ value: 'armed', label: 'Armed' },
					{ value: 'off', label: 'Off' }
				]
			}
		];
	}
</script>

<script lang="ts">
	import { TruncatedText } from '../ui/truncated-text/index.js';
	import { CELL_TEXT } from '../../text-shell.js';
	// The automations screen: one table of rules, in precedence order.
	//
	// The rule tier is deliberately the CHEAP tier below the flow builder — a table, not a canvas.
	// Everything a firm needs to answer "what will the machine do, and in what order" is on one row:
	// the trigger in English, the conditions as a sentence, what it does, whether it is armed, and
	// when it last did anything.
	//
	// **Order is precedence, and the screen says so.** Exactly one automation fires per event
	// (first satisfiable wins), so the row order is a product fact rather than a view preference —
	// which is why the table's column sorting is DISABLED (a sortable listing whose order carries
	// meaning would lie the moment you clicked a header) and why the numbered first column exists.
	//
	// **Why reordering is its own mode rather than a drag handle in the table:** `DataTable` owns
	// its `<tbody>`, and `dndzone` must be applied to the element that directly contains the
	// draggable children. Rather than fork the shared table (or hand-roll a second one and lose its
	// column/responsive behaviour), the screen swaps to the house drag list — the pages-rail /
	// layout-builder pattern, grip-handle-gated — for as long as you are reordering, then posts the
	// whole order in one go. Precedence changes are rare and consequential; a deliberate mode suits
	// them better than a drag you can start by accident while reading.
	import { untrack } from 'svelte';
	import { dndzone, type DndEvent } from 'svelte-dnd-action';
	import { flip } from 'svelte/animate';
	import GripVerticalIcon from '@lucide/svelte/icons/grip-vertical';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import ZapIcon from '@lucide/svelte/icons/zap';
	import SearchXIcon from '@lucide/svelte/icons/search-x';
	import PauseIcon from '@lucide/svelte/icons/pause';
	import PlayIcon from '@lucide/svelte/icons/play';
	import ArrowUpDownIcon from '@lucide/svelte/icons/arrow-up-down';
	import { Alert, AlertDescription, AlertTitle } from '../ui/alert/index.js';
	import { Badge } from '../ui/badge/index.js';
	import { Button } from '../ui/button/index.js';
	import { Card } from '../ui/card/index.js';
	import { DataTable } from '../ui/data-table/index.js';
	import type { DataTableColumn } from '../ui/data-table/index.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import { Switch } from '../ui/switch/index.js';
	import PageHeader from '../shell/page-header.svelte';
	import { ALL, ListingToolbar, matches, search } from '../book/listing/index.js';
	import { withToast } from '../book/clients/form-enhance.js';
	import AutomationSheet from './automation-sheet.svelte';
	import {
		formatWhen,
		outcomeLabel,
		outcomeTone,
		summariseConditions,
		summariseTarget
	} from './summary.js';
	import type {
		AutomationActions,
		AutomationLinks,
		AutomationRow,
		AutomationSettings,
		FlowTarget
	} from './types.js';

	let {
		automations,
		settings = null,
		flows = [],
		links,
		actions,
		loadFailed = false
	}: {
		automations: AutomationRow[];
		settings?: AutomationSettings | null;
		flows?: FlowTarget[];
		links: AutomationLinks;
		/** Absent (or partial) ⇒ the matching affordance simply isn't rendered — no control lies. */
		actions?: AutomationActions;
		loadFailed?: boolean;
	} = $props();

	const canWrite = $derived(Boolean(actions?.create ?? actions?.update));
	const canReorder = $derived(Boolean(actions?.reorder) && automations.length > 1);
	const canPause = $derived(Boolean(actions?.pause));
	const paused = $derived(settings?.paused ?? false);
	const atCap = $derived(settings !== null && automations.length >= settings.max_rules);

	let sheetOpen = $state(false);
	let editing = $state<AutomationRow | null>(null);
	let reordering = $state(false);
	let pending = $state(false);

	function openNew() {
		editing = null;
		sheetOpen = true;
	}
	function openEdit(row: AutomationRow) {
		editing = row;
		sheetOpen = true;
	}

	// ── Reorder mode (the house dnd pattern) ────────────────────────────────
	// A local mirror re-seeded on a genuinely new parent value (signature compare, like the pages
	// rail) so our own save echoed back never clobbers an in-flight drag.
	function signature(rows: AutomationRow[]): string {
		return rows.map((r) => r.uid).join('|');
	}
	let order = $state<AutomationRow[]>(untrack(() => [...automations]));
	let lastSig = $state(untrack(() => signature(automations)));
	$effect(() => {
		const sig = signature(automations);
		if (sig !== lastSig) {
			lastSig = sig;
			order = [...automations];
		}
	});

	// Drag only starts from the grip, so reading a row never becomes an accidental precedence
	// change (the layout-builder `dragDisabled` pattern).
	let dragDisabled = $state(true);
	function startDrag(e: PointerEvent) {
		if (e.pointerType === 'mouse' && e.button !== 0) return;
		dragDisabled = false;
	}
	function handleConsider(e: CustomEvent<DndEvent<AutomationRow>>) {
		order = e.detail.items;
	}
	function handleFinalize(e: CustomEvent<DndEvent<AutomationRow>>) {
		order = e.detail.items;
		dragDisabled = true;
	}
	const dndOptions = $derived({
		items: order,
		flipDurationMs: 160,
		dragDisabled,
		dropTargetStyle: {}
	});

	function cancelReorder() {
		order = [...automations];
		reordering = false;
	}

	// ── The arm/disarm toggle ───────────────────────────────────────────────
	// ONE form for every row rather than a form per row: `use:enhance` evaluates its argument once
	// at mount, and the row's identity is therefore attached inside the submit handler, read from
	// state at submit time. Rendering the uid into a hidden input and calling requestSubmit() in the
	// same tick is the documented stale-body bug (book/journeys `attachStepAnswers`).
	let toggleForm = $state<HTMLFormElement | null>(null);
	let toggleTarget = $state<AutomationRow | null>(null);

	function requestToggle(row: AutomationRow) {
		toggleTarget = row;
		toggleForm?.requestSubmit();
	}

	const toggleSubmit = withToast(
		(data) => (data?.enabled ? 'Automation armed.' : 'Automation disarmed.'),
		{ pending: (v) => (pending = v) }
	);

	// ── Search + status filter ──────────────────────────────────────────────
	// CLIENT-SIDE, over the whole `automations` array — it already arrives whole (capped at
	// `maxRulesPerTenant`, 200 today) and there is no server paging to defer to.
	//
	// Filtering narrows which rows the TABLE renders; it never reorders or copies `automations`.
	// That matters more here than on an ordinary listing: the array's order IS precedence (see
	// the header comment), so a filter that reshuffled it — or that fed a re-indexed copy to the
	// `#` column — would silently rewrite what a rule's number means. `filteredRows` below is a
	// same-object subsequence of `automations`, and the `#` cell still asks `automations.indexOf`
	// for the row's TRUE position, so a filtered rule's slot simply disappears from view rather
	// than pulling the rows beneath it up a number.
	let query = $state('');
	let enabledFilter = $state(ALL);
	const dirty = $derived(query !== '' || enabledFilter !== ALL);
	function clearFilters() {
		query = '';
		enabledFilter = ALL;
	}
	const filterDefs = automationFilterDefs();
	const barValue = $derived<Record<string, string[]>>(
		enabledFilter === ALL ? {} : { enabled: [enabledFilter] }
	);
	function applyFilters(next: Record<string, string[]>): void {
		enabledFilter = next.enabled?.[0] ?? ALL;
	}
	// Falls back to the raw key when there is no friendly label, matching what the "When" column
	// itself displays (`row.trigger_label ?? row.trigger_key`) — searching should find whatever a
	// reader can actually see on the row, not just the subset of rows with a registered label.
	const filteredRows = $derived(
		automations.filter(
			(r) =>
				matches(enabledFilter, r.enabled ? 'armed' : 'off') &&
				search(query, r.name, r.trigger_label ?? r.trigger_key)
		)
	);

	// ── Columns ─────────────────────────────────────────────────────────────
	// `sortable` is off on every column, deliberately — see the header comment.
	const columns: DataTableColumn<AutomationRow>[] = [
		{
			key: 'order',
			header: '#',
			headerClass: 'w-10',
			cellClass: 'text-muted-foreground w-10 tabular-nums'
		},
		{ key: 'name', header: 'Automation', headerClass: 'w-[22%]' },
		{ key: 'trigger', header: 'When', hideBelowWidth: 'lg', headerClass: 'w-[16%]' },
		{ key: 'conditions', header: 'Only if', hideBelowWidth: '2xl', headerClass: 'w-[16%]' },
		{ key: 'target', header: 'Then', headerClass: 'w-[18%]' },
		{ key: 'last', header: 'Last run', hideBelowWidth: 'xl', headerClass: 'w-[16%]' },
		// `min-w-36`: a Switch and, for a writer, an Edit button side by side.
		{ key: 'armed', header: 'Armed', align: 'center', headerClass: 'w-[12%] min-w-36' }
	];
</script>

{#snippet noAutomationMatches()}
	<!-- The nothing-yet state four hundred lines up is a full EmptyState with an action; this one
	     was a bare sentence, so the narrowed list was the only place on the page a reader could
	     arrive and find nothing offered. `clearFilters` was already wired to the toolbar. -->
	<EmptyState
		icon={SearchXIcon}
		title="No automations match your filters"
		description="Try a different search or status, or clear the filters to see all {automations.length}."
		action={clearAutomationFilters}
		compact
		chromeless
	/>
{/snippet}

{#snippet clearAutomationFilters()}
	<Button variant="outline" size="sm" onclick={clearFilters}>Clear filters</Button>
{/snippet}

{#snippet headerActions()}
	{#if canReorder}
		<Button
			variant={reordering ? 'default' : 'outline'}
			size="sm"
			onclick={() => (reordering ? cancelReorder() : (reordering = true))}
		>
			<ArrowUpDownIcon class="size-4" />
			{reordering ? 'Stop reordering' : 'Reorder'}
		</Button>
	{/if}
	{#if canWrite}
		<Button size="sm" onclick={openNew} disabled={atCap}>
			<PlusIcon class="size-4" /> New automation
		</Button>
	{/if}
{/snippet}

<div class="space-y-4">
	<PageHeader
		title="Automations"
		description="When something happens and your conditions hold, start a journey or perform one action. Rules run top to bottom and the first match wins."
		actions={canReorder || canWrite ? headerActions : undefined}
	/>

	{#if loadFailed}
		<Alert variant="destructive">
			<AlertTitle>Automations couldn’t be loaded</AlertTitle>
			<AlertDescription>Reload the page to try again.</AlertDescription>
		</Alert>
	{/if}

	{#if paused}
		<!-- Unmistakable: while this is up, NOTHING on this page runs, however armed it looks. -->
		<Alert variant="destructive">
			<AlertTitle>Automations are paused for this firm</AlertTitle>
			<AlertDescription>
				<p>
					No automation will fire until this is released — armed or not.{#if settings?.paused_reason}
						Reason given: “{settings.paused_reason}”.{/if}
				</p>
				{#if canPause && actions?.pause}
					<form
						method="POST"
						action={`?/${actions.pause}`}
						use:actions.enhance={withToast('Automations resumed.')}
						class="mt-3"
					>
						<input type="hidden" name="paused" value="" />
						<Button type="submit" size="sm" variant="outline">
							<PlayIcon class="size-4" /> Resume automations
						</Button>
					</form>
				{/if}
			</AlertDescription>
		</Alert>
	{/if}

	{#if atCap && settings}
		<Alert>
			<AlertDescription>
				This firm has reached its limit of {settings.max_rules} automations. Delete one before adding
				another.
			</AlertDescription>
		</Alert>
	{/if}

	{#if automations.length === 0}
		<EmptyState
			icon={ZapIcon}
			title="No automations yet"
			description="Automate routine work, such as chasing an unsigned form or starting an annual review."
			action={canWrite ? emptyAction : undefined}
		/>
	{:else if reordering && actions?.reorder}
		<Card class="p-4">
			<p class="text-muted-foreground mb-3 text-sm">
				Drag to set precedence. When an event matches more than one automation, the highest in this
				list is the one that runs.
			</p>
			<ul
				use:dndzone={dndOptions}
				onconsider={handleConsider}
				onfinalize={handleFinalize}
				class="space-y-1"
			>
				{#each order as row (row.uid)}
					<li
						animate:flip={{ duration: 160 }}
						class="bg-card flex items-center gap-3 rounded-md border px-3 py-2"
					>
						<button
							type="button"
							class="text-muted-foreground hover:text-foreground cursor-grab"
							aria-label={`Reorder ${row.name}`}
							onpointerdown={startDrag}
						>
							<GripVerticalIcon class="size-4" />
						</button>
						<span class="min-w-0 flex-1 truncate text-sm font-medium">{row.name}</span>
						<span class="text-muted-foreground hidden truncate text-xs sm:block">
							{row.trigger_label ?? row.trigger_key}
						</span>
						{#if !row.enabled}<Badge variant="outline">Off</Badge>{/if}
					</li>
				{/each}
			</ul>
			<form
				method="POST"
				action={`?/${actions.reorder}`}
				use:actions.enhance={withToast('Order saved.', {
					pending: (v) => (pending = v),
					onSuccess: () => (reordering = false)
				})}
				class="mt-4 flex gap-2"
			>
				<input type="hidden" name="uids" value={order.map((r) => r.uid).join(',')} />
				<Button type="submit" size="sm" disabled={pending}>Save order</Button>
				<Button type="button" size="sm" variant="ghost" onclick={cancelReorder}>Cancel</Button>
			</form>
		</Card>
	{:else}
		<Card class="p-0">
			<!-- Search + status filter narrow the TABLE only. Reorder mode above is a separate branch
			     seeded straight from `automations` (see `order`'s own comment), so it is untouched by
			     whatever is typed here: entering it always hands you the complete, true-precedence
			     list to drag, never a filtered slice — which is what keeps a drag move well-defined.
			     Disabling the Reorder button while a filter is active was considered and rejected: it
			     would only be protecting against a problem the architecture already doesn't have. -->
			<div class="border-b p-4">
				<ListingToolbar
					bind:query
					placeholder="Search automations or triggers"
					{dirty}
					onclear={clearFilters}
					{filterDefs}
					filterValue={barValue}
					onfilters={applyFilters}
				/>
			</div>
			<DataTable
				{columns}
				rows={filteredRows}
				getKey={(row) => row.uid}
				label="Automations"
				sortDisabled
				fixed
				rowClass={(row) => (row.enabled && !paused ? '' : 'opacity-60')}
				empty={noAutomationMatches}
			>
				{#snippet cell(row, column)}
					{#if column.key === 'order'}
						{automations.indexOf(row) + 1}
					{:else if column.key === 'name'}
						<div class="flex flex-col gap-0.5">
							<a href={links.automation(row.uid)} class="font-medium hover:underline">
								{row.name}
							</a>
							<div class="flex flex-wrap gap-1">
								{#if !row.supported}
									<!-- Stored, armed-looking, and unable to fire. Saying so here is the whole
									     reason the API returns the flag. -->
									<Badge variant="outline">Not live yet</Badge>
								{/if}
								{#if row.allow_automation_source}
									<Badge variant="outline">Chains</Badge>
								{/if}
							</div>
						</div>
					{:else if column.key === 'trigger'}
						<!-- A trigger reads as a sentence ("A client's bank details changed"), and falls back to
						     the raw key, which is longer still. Free text we did not author — CELL_TEXT. -->
						<TruncatedText
							text={row.trigger_label ?? row.trigger_key}
							class="{CELL_TEXT} text-muted-foreground text-sm"
						/>
					{:else if column.key === 'conditions'}
						<span class="text-muted-foreground line-clamp-2 text-sm">
							{summariseConditions(row.conditions)}
						</span>
					{:else if column.key === 'target'}
						<span class="text-sm">{summariseTarget(row)}</span>
					{:else if column.key === 'last'}
						{#if row.last_outcome}
							<div class="flex flex-col items-start gap-1">
								<StatusBadge tone={outcomeTone(row.last_outcome)}>
									{outcomeLabel(row.last_outcome)}
								</StatusBadge>
								<span class="text-muted-foreground text-xs">{formatWhen(row.last_fired_at)}</span>
							</div>
						{:else}
							<span class="text-muted-foreground text-xs">Never run</span>
						{/if}
					{:else if column.key === 'armed'}
						<div class="flex items-center justify-center gap-2">
							{#if actions?.toggle}
								<Switch
									checked={row.enabled}
									disabled={pending}
									aria-label={`${row.enabled ? 'Disarm' : 'Arm'} ${row.name}`}
									onCheckedChange={() => requestToggle(row)}
								/>
							{:else}
								<StatusBadge tone={row.enabled ? 'success' : 'neutral'}>
									{row.enabled ? 'Armed' : 'Off'}
								</StatusBadge>
							{/if}
							{#if canWrite}
								<Button variant="ghost" size="sm" onclick={() => openEdit(row)}>Edit</Button>
							{/if}
						</div>
					{/if}
				{/snippet}
			</DataTable>
		</Card>
	{/if}

	{#if !paused && canPause && actions?.pause && automations.length > 0}
		<!-- The kill switch lives at the bottom, past the rules it stops: it is a break-glass
		     control, not a filter. -->
		<Card class="flex flex-wrap items-center justify-between gap-3 p-4">
			<div>
				<p class="text-sm font-medium">Pause all automations</p>
				<p class="text-muted-foreground text-sm">
					Stops every automation for this firm immediately. Nothing is lost — armed rules simply
					don’t fire until you resume.
				</p>
			</div>
			<form
				method="POST"
				action={`?/${actions.pause}`}
				use:actions.enhance={withToast('Automations paused.')}
			>
				<input type="hidden" name="paused" value="1" />
				<Button type="submit" variant="outline" size="sm">
					<PauseIcon class="size-4" /> Pause
				</Button>
			</form>
		</Card>
	{/if}
</div>

{#if actions?.toggle}
	<!-- The single arm/disarm form. Its body is built in the submit handler from `toggleTarget`,
	     never from rendered inputs. -->
	<form
		bind:this={toggleForm}
		method="POST"
		action={`?/${actions.toggle}`}
		class="hidden"
		use:actions.enhance={(input) => {
			if (!toggleTarget) {
				input.cancel();
				return;
			}
			input.formData.set('uid', toggleTarget.uid);
			input.formData.set('enabled', toggleTarget.enabled ? '' : '1');
			return toggleSubmit(input);
		}}
	></form>
{/if}

{#snippet emptyAction()}
	<Button onclick={openNew}><PlusIcon class="size-4" /> New automation</Button>
{/snippet}

<AutomationSheet
	bind:open={sheetOpen}
	automation={editing}
	{flows}
	{actions}
	defaultRearmMinutes={Number(settings?.effective?.default_rearm_minutes ?? 1440)}
/>
