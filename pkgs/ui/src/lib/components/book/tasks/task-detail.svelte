<script lang="ts">
	// The task record — the platform portal's /tasks/[uid] page, extracted so the admin portal can
	// render the same view (the portal waterfall). Data arrives as typed props, every href comes
	// from the injected `links`, and the status transitions/edit forms only exist when the app
	// passes `actions` — no `actions` ⇒ a read-only record.
	import CheckIcon from '@lucide/svelte/icons/check';
	import XIcon from '@lucide/svelte/icons/x';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import PlayIcon from '@lucide/svelte/icons/play';
	import UserIcon from '@lucide/svelte/icons/user';
	import WorkflowIcon from '@lucide/svelte/icons/workflow';
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Badge } from '../../ui/badge/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import * as Dialog from '../../ui/dialog/index.js';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import { SectionHeading } from '../../ui/section-heading/index.js';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import PageHeader from '../../shell/page-header.svelte';
	import { withToast, type Enhance } from '../clients/form-enhance.js';
	import {
		ASSIGNEE_KIND_LABELS,
		CATEGORY_LABELS,
		dueLabel,
		isOpen,
		isOverdue,
		SOURCE_KIND_LABELS,
		type TaskRow
	} from './tasks.js';
	import type { AssigneeOption, TaskActivity, TaskDetailLinks } from './types.js';
	import { LINK_RECORD } from '../../../text-shell.js';

	/** The mutations this record can host. Absent ⇒ read-only. `complete` is available to anyone
	 *  who can see the task; `start`/`cancel`/`edit` only when the viewer can manage tasks, so the
	 *  app passes them conditionally. The app owns its form-action names. */
	export type TaskDetailActions = {
		/** SvelteKit's `enhance`, injected because pkgs/ui cannot import `$app/forms`. */
		enhance: Enhance;
		/** The mark-done action, e.g. `?/complete`. */
		complete: string;
		/** Pick-the-task-up (`in_progress`), e.g. `?/start`. */
		start?: string;
		/** Cancel the task, e.g. `?/cancel`. */
		cancel?: string;
		/** Edit / reassign, e.g. `?/edit`. */
		edit?: string;
	};

	let {
		task,
		activity = [],
		/** People/teams the edit dialog can hand the task to (picker shape, `user:`/`role:`). */
		assignees = [],
		links,
		actions = null,
		/** The latest action failure (the app's ActionData), for the non-JS fallback path. */
		formError = null
	}: {
		task: TaskRow;
		activity?: TaskActivity[];
		assignees?: AssigneeOption[];
		links: TaskDetailLinks;
		actions?: TaskDetailActions | null;
		formError?: string | null;
	} = $props();

	// `use:` needs a plain identifier: alias the injected enhance (never called when read-only —
	// every form is inside an `{#if actions}` branch, so the noop fallback only satisfies types).
	const enhanceForm: Enhance = $derived(actions?.enhance ?? (() => {}));

	const t = $derived(task);

	// The client-record hrefs. Both are null unless the app passed a `client` builder AND the row
	// carries the contact's UID — the uid is the whole reason these are possible: the row's
	// `assignee_contact_id`/`subject_id` are SERIALS and `/clients/[uid]` resolves by uid, so
	// before the API joined the uid on there was nothing to build an href out of. Null ⇒ the names
	// render as the plain text they always have, never as a link to nowhere.
	const assigneeClientHref = $derived(
		t.assignee_kind === 'contact' && links.client && t.assignee_contact_uid
			? links.client(t.assignee_contact_uid)
			: null
	);
	/** The client the task is ABOUT — a different person from the assignee more often than not
	 *  (a chase-the-form task is about the client and assigned to the adviser). */
	const subjectClientHref = $derived(
		t.subject_type === 'contact' && links.client && t.subject_contact_uid
			? links.client(t.subject_contact_uid)
			: null
	);

	// Pending flags so the header buttons disable while a mutation is in flight.
	let completing = $state(false);
	let cancelling = $state(false);
	let starting = $state(false);
	let saving = $state(false);

	// Edit / reassign. The API has had all of this since phase 1 and no surface exposed any of it, so
	// a due date couldn't be moved and a task couldn't be handed to a colleague at all.
	let editOpen = $state(false);
	let editTitle = $state('');
	let editDescription = $state('');
	let editPriority = $state('normal');
	let editDue = $state('');
	let editAssignee = $state('');
	let editFormEl = $state<HTMLFormElement | null>(null);

	const PRIORITY_ITEMS = [
		{ value: 'low', label: 'Low' },
		{ value: 'normal', label: 'Normal' },
		{ value: 'high', label: 'High' }
	];
	/** The task's current assignee as a picker value, when it is one the picker can represent. */
	const currentAssignee = $derived(
		t.assignee_kind === 'user' && t.assignee_user_id ? `user:${t.assignee_user_id}` : ''
	);
	function openEdit() {
		editTitle = t.title;
		editDescription = t.description ?? '';
		editPriority = t.priority;
		editDue = t.due_date ?? '';
		editAssignee = currentAssignee;
		editOpen = true;
	}

	// Cancelling is irreversible — confirm before submitting the cancel form.
	let cancelOpen = $state(false);
	let cancelFormEl = $state<HTMLFormElement | null>(null);

	const STATUS_TONE = {
		open: 'info',
		in_progress: 'info',
		done: 'success',
		cancelled: 'neutral'
	} as const;
	const STATUS_LABEL = {
		open: 'Open',
		in_progress: 'In progress',
		done: 'Done',
		cancelled: 'Cancelled'
	} as const;

	// One human line per audit event: creation, then per-column changes worth narrating.
	function activityLine(a: TaskActivity): string {
		const who = a.actor_name ?? a.actor_email ?? 'System';
		if (a.action.endsWith('.insert')) {
			return t.source_kind === 'flow' ? 'Raised by a journey step' : `Created by ${who}`;
		}
		if (a.action.endsWith('.update')) {
			const changed = (a.detail?.changed ?? {}) as Record<string, { old: unknown; new: unknown }>;
			if (changed.status) {
				const to = String(changed.status.new);
				if (to === 'done') return `Marked done by ${who}`;
				if (to === 'cancelled') return `Cancelled by ${who}`;
				return `Moved to ${STATUS_LABEL[to as keyof typeof STATUS_LABEL] ?? to} by ${who}`;
			}
			if (changed.assignee_kind || changed.assignee_user_id || changed.assignee_role_id) {
				return `Reassigned by ${who}`;
			}
			const cols = Object.keys(changed).filter((k) => !k.endsWith('_at') && k !== 'updated_at');
			if (cols.length) return `${who} updated ${cols.join(', ').replaceAll('_', ' ')}`;
			return `Updated by ${who}`;
		}
		return a.action;
	}
	function when(iso: string): string {
		return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
	}
	// Notification stamps produce updates nobody needs to read; keep the trail meaningful.
	const trail = $derived(
		activity.filter((a) => {
			const changed = Object.keys((a.detail?.changed as object) ?? {});
			return !(
				a.action.endsWith('.update') &&
				changed.length > 0 &&
				changed.every((k) => k.endsWith('notified_at') || k === 'updated_at')
			);
		})
	);
</script>

<!-- Declared OUTSIDE the PageHeader tag: an inline `{#snippet actions()}` would shadow the
     component's own `actions` prop inside the header's children scope. -->
{#snippet headerActions()}
	<div class="flex items-center gap-2">
		<StatusBadge tone={STATUS_TONE[t.status]}>{STATUS_LABEL[t.status]}</StatusBadge>
		{#if isOpen(t) && actions}
			{#if actions.edit}
				<Button variant="ghost" size="sm" onclick={openEdit}>
					<PencilIcon class="mr-1 size-3.5" /> Edit
				</Button>
			{/if}
			{#if t.status === 'open' && actions.start}
				<form
					method="POST"
					action={actions.start}
					use:enhanceForm={withToast('Marked in progress', { pending: (v) => (starting = v) })}
				>
					<Button type="submit" variant="ghost" size="sm" disabled={starting}>
						<PlayIcon class="mr-1 size-3.5" />
						{starting ? 'Starting…' : 'Start'}
					</Button>
				</form>
			{/if}
			<form
				method="POST"
				action={actions.complete}
				use:enhanceForm={withToast('Task completed', { pending: (v) => (completing = v) })}
			>
				<Button type="submit" variant="outline" size="sm" disabled={completing}>
					<CheckIcon class="mr-1 size-3.5" />
					{completing ? 'Marking…' : 'Mark done'}
				</Button>
			</form>
			{#if actions.cancel}
				<form
					method="POST"
					action={actions.cancel}
					bind:this={cancelFormEl}
					use:enhanceForm={withToast('Task cancelled', { pending: (v) => (cancelling = v) })}
				>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						class="text-muted-foreground hover:text-destructive"
						disabled={cancelling}
						onclick={() => (cancelOpen = true)}
					>
						<XIcon class="mr-1 size-3.5" />
						{cancelling ? 'Cancelling…' : 'Cancel'}
					</Button>
				</form>
			{/if}
		{/if}
	</div>
{/snippet}

<!-- FULL WIDTH, like the client and account records. This was `mx-auto max-w-3xl`, which on a
     desktop left a 768px column of content in the middle of a 1600px pane with empty gutters
     either side — and it read as a different kind of page from every other record in the book,
     which is the one thing a detail screen should not do. The card inside is already a
     two-column grid at `sm`, so it uses the width rather than stretching two fields across it. -->
<div class="w-full">
	<PageHeader
		title={t.title}
		back={{ href: links.tasks(), label: 'Tasks' }}
		actions={headerActions}
	/>

	{#if formError}
		<Alert variant="destructive" class="mb-4">
			<AlertDescription>{formError}</AlertDescription>
		</Alert>
	{/if}

	<Card glass class="mb-6">
		<CardContent class="grid gap-x-6 gap-y-3 sm:grid-cols-2">
			<div>
				<p class="text-muted-foreground text-xs font-medium">Assigned to</p>
				<p class="text-xs">
					{ASSIGNEE_KIND_LABELS[t.assignee_kind]}:
					{#if assigneeClientHref}
						<a href={assigneeClientHref} class={LINK_RECORD}>
							{t.assignee_name ?? '—'}
						</a>
					{:else}
						{t.assignee_name ?? '—'}
					{/if}
				</p>
			</div>
			<div>
				<p class="text-muted-foreground text-xs font-medium">Category</p>
				<p class="text-xs">
					{CATEGORY_LABELS[t.category]}
					{#if t.priority === 'high'}<Badge variant="destructive" class="ml-1">High</Badge>{/if}
					{#if t.priority === 'low'}<span class="text-muted-foreground"> · low priority</span>{/if}
				</p>
			</div>
			<div>
				<p class="text-muted-foreground text-xs font-medium">Due</p>
				<p class="text-xs {isOverdue(t) ? 'text-destructive font-medium' : ''}">
					{t.due_date ? `${t.due_date} · ${dueLabel(t)}` : 'No due date'}
				</p>
			</div>
			<div>
				<p class="text-muted-foreground text-xs font-medium">Source</p>
				<p class="flex items-center gap-1.5 text-xs">
					{#if t.subject_type === 'execution' && t.subject_id}
						<WorkflowIcon class="text-muted-foreground size-3.5" />
						<a href={links.journey(t.subject_id)} class="text-brand hover:underline">
							Raised by a journey
						</a>
					{:else if t.source_kind === 'manual'}
						Created by hand
					{:else}
						{SOURCE_KIND_LABELS[t.source_kind]}
					{/if}
				</p>
			</div>
			<!-- WHO the task is about. Its own field rather than a branch of "Source" above, because
			     the two answer different questions: a manual chase raised about a client is still
			     "Created by hand", and folding the client in there would have replaced that. Linked
			     exactly like the journey subject beside it — the only difference is that a journey
			     subject travels as a uid already and a contact subject travels as a serial, which is
			     why the record could link one and not the other. -->
			{#if t.subject_type === 'contact' && t.subject_contact_name}
				<div>
					<p class="text-muted-foreground text-xs font-medium">About</p>
					<p class="flex items-center gap-1.5 text-sm">
						<UserIcon class="text-muted-foreground size-3.5" />
						{#if subjectClientHref}
							<a href={subjectClientHref} class={LINK_RECORD}>
								{t.subject_contact_name}
							</a>
						{:else}
							{t.subject_contact_name}
						{/if}
					</p>
				</div>
			{/if}
			{#if t.description}
				<div class="sm:col-span-2">
					<p class="text-muted-foreground text-xs font-medium">Notes</p>
					<p class="text-xs whitespace-pre-wrap">{t.description}</p>
				</div>
			{/if}
		</CardContent>
	</Card>

	{#if trail.length}
		<section>
			<SectionHeading title="Activity" count={trail.length} />
			<ol class="border-border relative ml-1 space-y-3 border-l pl-4">
				{#each trail as a (a.id)}
					<li class="relative">
						<span
							class="bg-brand ring-background absolute top-1.5 -left-[21px] size-2 rounded-full ring-2"
						></span>
						<p class="text-xs">{activityLine(a)}</p>
						<p class="text-muted-foreground text-xs">{when(a.created_at)}</p>
					</li>
				{/each}
			</ol>
		</section>
	{/if}
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

<!-- Edit / reassign. Handing a task over clears the notification stamps server-side, so the new
	 assignee is actually emailed rather than inheriting the old one's "already told" flag. -->
{#if actions?.edit}
	<Dialog.Root bind:open={editOpen}>
		<Dialog.Content class="sm:max-w-lg">
			<Dialog.Header>
				<Dialog.Title>Edit task</Dialog.Title>
				<Dialog.Description>Change the details, or hand it to someone else.</Dialog.Description>
			</Dialog.Header>
			<form
				method="POST"
				action={actions.edit}
				bind:this={editFormEl}
				class="space-y-4"
				use:enhanceForm={withToast('Task updated', {
					pending: (v) => (saving = v),
					onSuccess: () => (editOpen = false),
					reset: false
				})}
			>
				<div class="space-y-1.5">
					<Label for="edit-title">Title</Label>
					<Input id="edit-title" name="title" bind:value={editTitle} />
				</div>
				<div class="space-y-1.5">
					<Label for="edit-description">Notes</Label>
					<Textarea
						id="edit-description"
						name="description"
						rows={3}
						bind:value={editDescription}
					/>
				</div>
				<div class="grid gap-3 sm:grid-cols-2">
					<div class="space-y-1.5">
						<Label>Priority</Label>
						<SelectMenu
							size="sm"
							items={PRIORITY_ITEMS}
							value={editPriority}
							onValueChange={(v) => (editPriority = v)}
						/>
						<input type="hidden" name="priority" value={editPriority} />
					</div>
					<div class="space-y-1.5">
						<Label for="edit-due">Due date</Label>
						<Input id="edit-due" name="due_date" type="date" bind:value={editDue} />
					</div>
				</div>
				<div class="space-y-1.5">
					<Label>Assign to</Label>
					{#if assignees.length}
						<SelectMenu
							size="sm"
							items={assignees}
							value={editAssignee}
							placeholder="Leave as it is…"
							onValueChange={(v) => (editAssignee = v)}
						/>
						<input type="hidden" name="assignee" value={editAssignee} />
						<!-- Linked here too: this hint is read at the moment of deciding whether to take the
						     task off a client, which is exactly when you want to look at who they are. -->
						<p class="text-muted-foreground text-xs">
							Currently {ASSIGNEE_KIND_LABELS[t.assignee_kind].toLowerCase()}:
							{#if assigneeClientHref}
								<a href={assigneeClientHref} class={LINK_RECORD}>
									{t.assignee_name ?? '—'}
								</a>
							{:else}
								{t.assignee_name ?? '—'}
							{/if}. The new assignee will be notified.
						</p>
					{:else}
						<p class="text-muted-foreground text-xs">
							No people or teams available to reassign to.
						</p>
					{/if}
				</div>
			</form>
			<Dialog.Footer>
				<Button variant="ghost" onclick={() => (editOpen = false)}>Cancel</Button>
				<Button disabled={saving} onclick={() => editFormEl?.requestSubmit()}>
					{saving ? 'Saving…' : 'Save changes'}
				</Button>
			</Dialog.Footer>
		</Dialog.Content>
	</Dialog.Root>
{/if}
