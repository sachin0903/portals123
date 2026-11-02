<script module lang="ts">
	import type { FilterDef } from '../listing/filter-panel.svelte';

	/** A pure function, not a `$derived`, so it can be pinned in a test without rendering the
	 *  panel: it lives behind the funnel's popover, which renders nothing server-side while shut. */
	export function taskFilterDefs(input: { categoryItems: FilterDef['options'] }): FilterDef[] {
		return [
			{ key: 'category', label: 'Category', options: input.categoryItems },
			{
				key: 'due',
				label: 'Due',
				options: [
					{ value: 'overdue', label: 'Overdue' },
					{ value: 'today', label: 'Due today' },
					{ value: 'week', label: 'Due this week' }
				]
			},
			// Must match `app/tasks/service.py::PRIORITIES` — the API 422s on anything else.
			{
				key: 'priority',
				label: 'Priority',
				options: [
					{ value: 'high', label: 'High' },
					{ value: 'normal', label: 'Normal' },
					{ value: 'low', label: 'Low' }
				]
			}
		];
	}
</script>

<script lang="ts">
	// The tasks inbox — the platform portal's /tasks page, extracted so the admin portal can
	// render the same queue (the portal waterfall: ops are a proxy middle-office).
	//
	// Data comes in as typed props (the app's loader fetches it), every href is built by the
	// injected `links`, and the mutations only exist when the app passes `actions` — no `actions`
	// ⇒ a read-only inbox (no create dialog, no Done/cancel buttons). Server-side filters and the
	// keyset "show older" window live in the app's URL, so the component only ASKS for them via
	// `onfilters`/`onshowmore` and the app owns the navigation.
	import PlusIcon from '@lucide/svelte/icons/plus';
	import CheckIcon from '@lucide/svelte/icons/check';
	import XIcon from '@lucide/svelte/icons/x';
	import ClipboardCheckIcon from '@lucide/svelte/icons/clipboard-check';
	import UserIcon from '@lucide/svelte/icons/user';
	import UsersIcon from '@lucide/svelte/icons/users';
	import ShieldIcon from '@lucide/svelte/icons/shield-check';
	import WorkflowIcon from '@lucide/svelte/icons/workflow';
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Badge } from '../../ui/badge/index.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import { DataTable } from '../../ui/data-table/index.js';
	import type { DataTableColumn } from '../../ui/data-table/index.js';
	import * as Dialog from '../../ui/dialog/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import { default as Segmented } from '../../ui/segmented/segmented.svelte';
	import { SelectMenu, type SelectMenuGroup } from '../../ui/select-menu/index.js';
	import { ListingToolbar } from '../listing/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import { withToast, type Enhance } from '../clients/form-enhance.js';
	import { LINK_RECORD } from '../../../text-shell.js';
	import {
		ASSIGNEE_KIND_LABELS,
		CATEGORY_LABELS,
		TASK_SCOPE_LABELS,
		byUrgency,
		dueLabel,
		isOpen,
		isOverdue,
		type TaskRow,
		type TaskScope
	} from './tasks.js';
	import type {
		TaskClientOption,
		TaskMemberOption,
		TaskRoleOption,
		TasksListingLinks
	} from './types.js';

	/** The mutations this inbox can host. Absent ⇒ read-only. `complete` is available to anyone
	 *  who can see the inbox; `create`/`cancel` only when the viewer can manage tasks, so the app
	 *  passes them conditionally. The app owns its form-action names; this component only posts
	 *  to them. */
	export type TasksListingActions = {
		/** SvelteKit's `enhance`, injected because pkgs/ui cannot import `$app/forms`. */
		enhance: Enhance;
		/** The row's mark-done action, e.g. `?/complete`. */
		complete: string;
		/** The create form's action, e.g. `?/create` — presence shows the "New task" dialog. */
		create?: string;
		/** The row's cancel action, e.g. `?/cancel` — presence shows the cancel button. */
		cancel?: string;
	};

	let {
		tasks,
		mineUids,
		/** How many OPEN tasks the other scope holds, for the tab you are not on. The app already
		 *  reads both scopes (it needs `mineUids` either way), so this costs nothing and is what
		 *  stops a scoped inbox hiding the firm's work without saying so. */
		otherOpenCount = 0,
		/** The server-side slice currently applied (the app derives it from its URL). */
		filters = { category: [], due: [], q: '', priority: [] },
		/** Keyset cursor for the next window; null ⇒ everything is already on the page. */
		nextBefore = null,
		members = [],
		roles = [],
		clients = [],
		links,
		actions = null,
		/** The latest action failure (the app's ActionData), for the non-JS fallback path. */
		formError = null,
		/** Asked for when the user changes a filter — the app pushes it into its URL. */
		onfilters,
		/** Asked for when the user wants the next keyset window. */
		onshowmore,
		onCreateOpen,
		initialCreate = false
	}: {
		tasks: TaskRow[];
		mineUids: ReadonlySet<string>;
		otherOpenCount?: number;
		/**
		 * `q` and `priority` join `category`/`overdue` as SERVER-side narrowings, because this
		 * inbox pages: it holds one 200-row window and keysets backwards through the rest, so a
		 * search over the rows in hand would answer "no task about the Ashworth transfer" when it
		 * means "not in the most recent 200".
		 */
		filters?: {
			category: string[];
			/** WHEN it is due, as windows: `overdue` / `today` / `week`. Several are an OR, because
			 *  "overdue or due today" is the morning's list. It replaced an `overdue` boolean, which
			 *  could only ever ask one of the three questions an inbox is read with. */
			due: string[];
			q?: string;
			priority?: string[];
			/** WHOSE tasks the rows are — applied by the API (`assignee=me`), not by this component.
			 *  Absent means the app does not scope at all: the ops mount reads a firm's book as an
			 *  outsider, where "mine" can only ever be empty, so it gets no scope control and every
			 *  task. */
			assignee?: TaskScope;
		};
		nextBefore?: number | null;
		members?: TaskMemberOption[];
		roles?: TaskRoleOption[];
		clients?: TaskClientOption[];
		links: TasksListingLinks;
		actions?: TasksListingActions | null;
		formError?: string | null;
		onfilters?: (next: {
			category: string[];
			due: string[];
			q: string;
			priority: string[];
			assignee: TaskScope;
		}) => void;
		onshowmore?: (before: number) => void;
		/** Fired when the New-task dialog opens, so the app can load assignee catalogues then. */
		onCreateOpen?: () => void | Promise<void>;
		/** Open the create dialog on arrival (used by global command palettes and deep links). */
		initialCreate?: boolean;
	} = $props();

	// `use:` needs a plain identifier: alias the injected enhance (never called when read-only —
	// every form is inside an `{#if actions}` branch, so the noop fallback only satisfies types).
	const enhanceForm: Enhance = $derived(actions?.enhance ?? (() => {}));

	// WHOSE and WHAT STATE are different questions, and one control answering both is what made
	// the scope pointless: picking "Done" threw the scope away, so a reader chasing their own work
	// landed on everyone's completed tasks.
	//
	// WHOSE is a server filter in the app's URL — it has to be, because the inbox pages. WHAT STATE
	// is a slice of the window in hand, which is honest: the rows are already scoped, and open
	// against done is a split of what arrived rather than a claim about the whole book.
	let state_ = $state<'open' | 'done'>('open');
	let dialogOpen = $state(false);
	$effect(() => {
		if (initialCreate) {
			dialogOpen = true;
			void onCreateOpen?.();
		}
	});

	// Absent means the app does not scope (the ops mount), so the control is not offered and every
	// task is shown — never a "mine" tab that can only be empty for somebody outside the firm.
	const scoped = $derived(filters.assignee !== undefined && onfilters !== undefined);
	const scope = $derived<TaskScope>(filters.assignee ?? 'all');

	// `.sort` (not `.toSorted`) because this package's tsconfig lib stops at ES2022 — the filter
	// already made a fresh array, so sorting in place mutates nothing the caller owns.
	const open = $derived(tasks.filter(isOpen).sort((a, b) => byUrgency(a, b)));
	const done = $derived(tasks.filter((t) => !isOpen(t)));
	const overdueCount = $derived(open.filter((t) => isOverdue(t)).length);
	const rows = $derived(state_ === 'open' ? open : done);

	// What an empty list should say next. Scoped to you with work sitting elsewhere, the useful
	// sentence names it and points at the other tab — otherwise the reader concludes the firm has
	// nothing on, which is the opposite of true.
	const emptyHint = $derived(
		state_ === 'done'
			? undefined
			: scope === 'me' && otherOpenCount > 0
				? `${otherOpenCount} open ${otherOpenCount === 1 ? 'task is' : 'tasks are'} assigned to other people. See ${TASK_SCOPE_LABELS.all}.`
				: actions?.create
					? 'Create one by hand, or let a journey step raise them automatically.'
					: undefined
	);

	const KIND_ICON = { user: UserIcon, role: ShieldIcon, contact: UsersIcon } as const;

	function journeyHref(t: TaskRow): string | null {
		return t.subject_type === 'execution' && t.subject_id ? links.journey(t.subject_id) : null;
	}
	/** The client record behind a task assigned to a contact — null unless the app gave a builder
	 *  AND the row carries the contact's UID. The uid is why this is possible at all: the row's
	 *  `assignee_contact_id` is a serial and `/clients/[uid]` resolves by uid, so until the API
	 *  joined the uid on there was nothing an href could be made of. Null ⇒ the name stays the
	 *  plain text it is today, rather than a link that goes nowhere. */
	function clientHref(t: TaskRow): string | null {
		return t.assignee_kind === 'contact' && links.client && t.assignee_contact_uid
			? links.client(t.assignee_contact_uid)
			: null;
	}

	// Row actions — disable the row's buttons while its mutation is in flight.
	let pendingUid = $state<string | null>(null);

	// Cancelling a task is irreversible — confirm, then submit the row's form.
	let cancelOpen = $state(false);
	let cancelFormEl: HTMLFormElement | null = null;

	// New-task dialog state (SelectMenu values live here; reset on each open).
	let creating = $state(false);
	let assignee = $state('');
	let category = $state('generic');
	let priority = $state('normal');
	function openDialog() {
		assignee = '';
		category = 'generic';
		priority = 'normal';
		dialogOpen = true;
		void onCreateOpen?.();
	}

	const assigneeItems: SelectMenuGroup[] = $derived([
		...(roles.length
			? [
					{
						label: 'Teams (roles)',
						options: roles.map((r) => ({ value: 'role:' + r.slug, label: r.name }))
					}
				]
			: []),
		...(members.length
			? [
					{
						label: 'Staff',
						options: members.map((m) => ({
							value: 'user:' + m.user_id,
							label: m.name ?? m.email
						}))
					}
				]
			: []),
		...(clients.length
			? [
					{
						label: 'Clients',
						options: clients.map((c) => ({
							value: 'contact:' + c.contact_id,
							label: c.name ?? c.email ?? `#${c.contact_id}`
						}))
					}
				]
			: [])
	]);
	const categoryItems = Object.entries(CATEGORY_LABELS).map(([value, label]) => ({
		value,
		label
	}));

	/** Merge a filter change into the current slice and hand it to the app (which owns the URL).
	 *
	 *  Every field is restated on each call, so changing the category cannot silently drop the
	 *  search term the way a partial patch would. */
	function applyFilters(patch: {
		category?: string[];
		due?: string[];
		q?: string;
		priority?: string[];
		assignee?: TaskScope;
	}): void {
		onfilters?.({
			category: patch.category ?? filters.category,
			due: patch.due ?? filters.due,
			q: patch.q ?? filters.q ?? '',
			priority: patch.priority ?? filters.priority ?? [],
			assignee: patch.assignee ?? scope
		});
	}

	/** True when anything beyond the Mine/All/Done tab is narrowing the inbox — drives Clear.
	 *  The tab is deliberately excluded: it is a view over the loaded window, not a filter
	 *  somebody set, and clearing it would move the reader to a different list. */
	const dirty = $derived(
		filters.category.length > 0 ||
			filters.due.length > 0 ||
			!!filters.q ||
			(filters.priority?.length ?? 0) > 0
	);

	/** Derived out here because the `{#snippet filterBar()}` below would shadow the `filters` PROP
	 *  inside its own scope — the same trap the listing kebab's `actions` snippet records. */
	const barValue = $derived<Record<string, string[]>>({
		...(filters.category.length ? { category: filters.category } : {}),
		...(filters.due.length ? { due: filters.due } : {}),
		...(filters.priority?.length ? { priority: filters.priority } : {})
	});

	const filterDefs = $derived(taskFilterDefs({ categoryItems }));

	/** Alphabetical would put low above normal. */
	const PRIORITY_RANK: Record<TaskRow['priority'], number> = { high: 3, normal: 2, low: 1 };

	/** No Status column: the Open/Done tabs say it. Unsorted on arrival, so `byUrgency` order holds. */
	const columns = $derived<DataTableColumn<TaskRow>[]>([
		{ key: 'kind', header: '', headerClass: 'w-16 pl-5', cellClass: 'pl-5' },
		{
			key: 'task',
			header: 'Task',
			sortable: true,
			headerClass: 'w-[30%]',
			sortValue: (t) => t.title.toLowerCase()
		},
		{
			key: 'assignee',
			header: 'Assignee',
			sortable: true,
			hideBelowWidth: 'lg',
			headerClass: 'w-[18%]',
			sortValue: (t) => (t.assignee_name ?? '').toLowerCase()
		},
		{
			key: 'category',
			header: 'Category',
			sortable: true,
			hideBelowWidth: '2xl',
			headerClass: 'w-[16%]',
			sortValue: (t) => CATEGORY_LABELS[t.category]
		},
		{
			key: 'due',
			header: 'Due',
			sortable: true,
			headerClass: 'w-[12%]',
			sortValue: (t) => t.due_date
		},
		{
			key: 'priority',
			header: 'Priority',
			sortable: true,
			defaultDir: 'desc',
			hideBelowWidth: 'xl',
			headerClass: 'w-[8%]',
			sortValue: (t) => PRIORITY_RANK[t.priority]
		},
		...(actions ? [{ key: 'actions', header: '', headerClass: 'w-[12%]' }] : [])
	]);
</script>

{#snippet clearTaskFilters()}
	<Button
		variant="outline"
		size="sm"
		onclick={() => applyFilters({ category: [], due: [], q: '', priority: [] })}
	>
		Clear filters
	</Button>
{/snippet}

<!-- Declared OUTSIDE the PageHeader tag: an inline `{#snippet actions()}` would shadow the
     component's own `actions` prop inside the header's children scope. -->

{#snippet taskInboxEmpty()}
	<EmptyState
		compact
		icon={ClipboardCheckIcon}
		title={dirty
			? 'No tasks match your filters'
			: state_ === 'done'
				? 'Nothing settled yet'
				: scope === 'me'
					? 'Nothing assigned to you'
					: 'No open tasks'}
		description={dirty ? 'Clear them to see everything in this inbox.' : emptyHint}
		action={dirty ? clearTaskFilters : actions?.create ? newTaskButton : undefined}
	/>
{/snippet}

{#snippet newTaskButton()}
	<Button onclick={openDialog}>
		<PlusIcon class="mr-1.5 size-4" /> New task
	</Button>
{/snippet}

<div class="w-full">
	{#if formError}
		<Alert variant="destructive" class="mb-4">
			<AlertDescription>{formError}</AlertDescription>
		</Alert>
	{/if}

	{#snippet scopeControls()}
		<div class="flex flex-wrap items-center gap-2">
			{#if scoped}
				<!-- WHOSE work this is. A server filter, so it narrows the whole book rather than the
				     window on screen, and it lives in the app's URL so it survives a reload. The count
				     on the tab you are NOT on is what stops a scoped inbox hiding the firm's work in
				     silence. -->
				<Segmented
					options={[
						{
							value: 'me',
							label: `${TASK_SCOPE_LABELS.me} (${scope === 'me' ? open.length : otherOpenCount})`
						},
						{
							value: 'all',
							label: `${TASK_SCOPE_LABELS.all} (${scope === 'all' ? open.length : otherOpenCount})`
						}
					]}
					value={scope}
					onValueChange={(v) => applyFilters({ assignee: v as TaskScope })}
				/>
			{/if}
			<Segmented
				options={[
					{ value: 'open', label: `Open (${open.length})` },
					{ value: 'done', label: `Done (${done.length})` }
				]}
				value={state_}
				onValueChange={(v) => (state_ = v as typeof state_)}
			/>
		</div>
		<!-- The count is a SIGNAL, not a control: it says the inbox has late work whether or not
			 anybody has filtered for it. The filters themselves are in the bar below — Category, Due
			 and Priority were three controls spread over two rows with the search between them. -->
		<div class="flex flex-wrap items-center gap-2">
			{#if overdueCount > 0}
				<Badge variant="destructive">{overdueCount} overdue</Badge>
			{/if}
		</div>
	{/snippet}

	<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
		<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
			{#if onfilters}
				<ListingToolbar
					class="shrink-0"
					stacked
					query={filters.q ?? ''}
					placeholder="Search tasks by title or description"
					{dirty}
					onclear={() => applyFilters({ category: [], due: [], q: '', priority: [] })}
					onsearch={(term) => applyFilters({ q: term })}
					filters={scopeControls}
					actions={actions?.create ? newTaskButton : undefined}
					{filterDefs}
					filterValue={barValue}
					onfilters={(next) =>
						applyFilters({
							category: next.category ?? [],
							due: next.due ?? [],
							priority: next.priority ?? []
						})}
				/>
			{:else}
				<!-- Read-only mounts have no filter callback but keep the view switches. -->
				<div class="flex min-h-9 shrink-0 flex-wrap items-center justify-between gap-3">
					{@render scopeControls()}
					{#if actions?.create}{@render newTaskButton()}{/if}
				</div>
			{/if}

			<DataTable {columns} {rows} getKey={(t) => t.uid} fixed label="Tasks" empty={taskInboxEmpty}>
				{#snippet cell(t: TaskRow, column: DataTableColumn<TaskRow>)}
					{#if column.key === 'kind'}
						{@const Icon = KIND_ICON[t.assignee_kind]}
						<div
							class="flex size-8 shrink-0 items-center justify-center rounded-lg {t.status ===
							'done'
								? 'bg-success/10 text-success'
								: isOverdue(t)
									? 'bg-destructive/10 text-destructive'
									: 'bg-muted text-muted-foreground'}"
						>
							<Icon class="size-4" />
						</div>
					{:else if column.key === 'task'}
						<div class="min-w-0">
							<span class="flex min-w-0 items-center gap-2">
								<a
									href={links.task(t.uid)}
									class="min-w-0 truncate font-medium hover:underline {isOpen(t)
										? ''
										: 'text-muted-foreground line-through'}"
								>
									{t.title}
								</a>
								{#if scope === 'all' && mineUids.has(t.uid)}
									<!-- Only on everyone's tab; `mineUids` also covers a role you hold. -->
									<Badge variant="secondary" class="shrink-0">Yours</Badge>
								{/if}
								{#if t.status === 'cancelled'}
									<Badge variant="outline" class="shrink-0">Cancelled</Badge>
								{/if}
							</span>
							{#if t.description}
								<p class="text-muted-foreground truncate text-xs" title={t.description}>
									{t.description}
								</p>
							{/if}
						</div>
					{:else if column.key === 'assignee'}
						<!-- A client assignee links to the client record when the mount can build it. -->
						<span class="block min-w-0 truncate">
							<span class="text-muted-foreground text-xs">
								{ASSIGNEE_KIND_LABELS[t.assignee_kind]}:
							</span>
							{#if clientHref(t)}
								<a href={clientHref(t)} class={LINK_RECORD}>
									{t.assignee_name ?? '—'}
								</a>
							{:else}
								{t.assignee_name ?? '—'}
							{/if}
						</span>
					{:else if column.key === 'category'}
						<span class="flex min-w-0 items-center gap-1.5">
							<Badge variant="outline" class="max-w-full min-w-0 shrink font-normal"
								><TruncatedText text={CATEGORY_LABELS[t.category]} /></Badge
							>
							{#if t.source_kind === 'flow'}
								<span class="text-muted-foreground truncate text-xs">raised by a journey</span>
							{/if}
							{#if journeyHref(t)}
								<a
									href={journeyHref(t)}
									class="text-muted-foreground hover:text-foreground shrink-0"
									title="Open the journey"
								>
									<WorkflowIcon class="size-4" />
								</a>
							{/if}
						</span>
					{:else if column.key === 'due'}
						{#if t.due_date && isOpen(t)}
							<span
								class="text-xs whitespace-nowrap {isOverdue(t)
									? 'text-destructive font-medium'
									: 'text-muted-foreground'}"
							>
								{dueLabel(t)}
							</span>
						{/if}
					{:else if column.key === 'priority'}
						{#if t.priority === 'high' && isOpen(t)}
							<Badge variant="destructive">High</Badge>
						{/if}
					{:else if column.key === 'actions'}
						{#if isOpen(t) && actions}
							<div class="flex items-center justify-end gap-1">
								<form
									method="POST"
									action={actions.complete}
									use:enhanceForm={withToast('Task completed', {
										pending: (v) => (pendingUid = v ? t.uid : null)
									})}
								>
									<input type="hidden" name="uid" value={t.uid} />
									<Button type="submit" variant="outline" size="sm" disabled={pendingUid === t.uid}>
										<CheckIcon class="mr-1 size-3.5" />
										{pendingUid === t.uid ? 'Working…' : 'Done'}
									</Button>
								</form>
								{#if actions.cancel}
									<form
										method="POST"
										action={actions.cancel}
										use:enhanceForm={withToast('Task cancelled', {
											pending: (v) => (pendingUid = v ? t.uid : null)
										})}
									>
										<input type="hidden" name="uid" value={t.uid} />
										<Button
											type="submit"
											variant="ghost"
											size="icon"
											class="text-muted-foreground hover:text-destructive size-8"
											title="Cancel task"
											disabled={pendingUid === t.uid}
											onclick={(e: MouseEvent) => {
												e.preventDefault();
												cancelFormEl = (e.currentTarget as HTMLButtonElement).form;
												cancelOpen = true;
											}}
										>
											<XIcon class="size-4" />
										</Button>
									</form>
								{/if}
							</div>
						{/if}
					{/if}
				{/snippet}
			</DataTable>

			{#if nextBefore != null}
				<div class="flex shrink-0 flex-col items-center gap-1">
					<p class="text-muted-foreground text-xs">
						Showing the most recent 200 tasks — there are older ones.
					</p>
					{#if onshowmore}
						<Button variant="outline" size="sm" onclick={() => onshowmore(nextBefore as number)}>
							Show older tasks
						</Button>
					{/if}
				</div>
			{/if}
		</CardContent>
	</Card>
</div>

<ConfirmDialog
	bind:open={cancelOpen}
	title="Cancel this task?"
	description="The task will be closed as cancelled for everyone it's assigned to."
	confirmLabel="Cancel task"
	cancelLabel="Keep task"
	destructive
	onconfirm={() => {
		cancelFormEl?.requestSubmit();
	}}
/>

{#if actions?.create}
	<Dialog.Root bind:open={dialogOpen}>
		<Dialog.Content class="sm:max-w-md">
			<Dialog.Header>
				<Dialog.Title>New task</Dialog.Title>
				<Dialog.Description>
					Assign an action item to a colleague, a team, or a client.
				</Dialog.Description>
			</Dialog.Header>
			<form
				method="POST"
				action={actions.create}
				class="space-y-4"
				use:enhanceForm={withToast('Task created', {
					pending: (v) => (creating = v),
					onSuccess: () => (dialogOpen = false)
				})}
			>
				<div class="space-y-1.5">
					<Label for="task-title">Title</Label>
					<Input id="task-title" name="title" required placeholder="e.g. Chase the transfer form" />
				</div>
				<div class="space-y-1.5">
					<Label for="task-assignee">Assign to</Label>
					<SelectMenu
						id="task-assignee"
						name="assignee"
						required
						placeholder="Choose…"
						items={assigneeItems}
						bind:value={assignee}
					/>
				</div>
				<div class="grid grid-cols-2 gap-3">
					<div class="space-y-1.5">
						<Label for="task-category">Category</Label>
						<SelectMenu
							id="task-category"
							name="category"
							items={categoryItems}
							bind:value={category}
						/>
					</div>
					<div class="space-y-1.5">
						<Label for="task-priority">Priority</Label>
						<SelectMenu
							id="task-priority"
							name="priority"
							items={[
								{ value: 'low', label: 'Low' },
								{ value: 'normal', label: 'Normal' },
								{ value: 'high', label: 'High' }
							]}
							bind:value={priority}
						/>
					</div>
				</div>
				<div class="space-y-1.5">
					<Label for="task-due"
						>Due date <span class="text-muted-foreground">(optional)</span></Label
					>
					<Input id="task-due" name="due_date" type="date" />
				</div>
				<div class="space-y-1.5">
					<Label for="task-desc">Notes <span class="text-muted-foreground">(optional)</span></Label>
					<Textarea id="task-desc" name="description" rows={2} />
				</div>
				<Dialog.Footer>
					<Button type="button" variant="outline" onclick={() => (dialogOpen = false)}
						>Cancel</Button
					>
					<Button type="submit" disabled={creating || !assignee}>
						{creating ? 'Creating…' : 'Create task'}
					</Button>
				</Dialog.Footer>
			</form>
		</Dialog.Content>
	</Dialog.Root>
{/if}
