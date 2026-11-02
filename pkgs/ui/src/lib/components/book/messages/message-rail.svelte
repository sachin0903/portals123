<script lang="ts" generics="T extends ThreadMessage">
	// The conversation's RAIL — who you are talking to, how to reach them, who else is on it and
	// what they hold. Shared by the client record's Messages section and the platform inbox, so the
	// same conversation carries the same context wherever it is read from.
	//
	// THE CLIENT AT THE TOP, THEN COLLAPSIBLE SECTIONS. The identity and its facts neither fold
	// nor scroll — "who am I talking to" is the question the rail is opened for. Everything under
	// it is a list the reader wants sometimes and out of the way the rest of the time, so each is a
	// band with a disclosure, in the order they are asked for: Accounts, Notes, Tasks, People.
	//
	// The FILES are not here: they are a tab on the conversation pane, because a file belongs to
	// the thread rather than to the client.
	//
	// IT DOES NOT COLLAPSE AS A WHOLE. The rail used to fold away behind one toggle, which put the
	// client's details one click away on every conversation. Only the breakpoint still varies:
	// below `lg` there is no room for two columns and it stacks under the thread.
	import { Badge, Button, ClientAvatar, EmptyState, PersonAvatar, cn } from '@graphene/ui';
	import NoteComposer from '../notes/composer.svelte';
	import StickyNoteIcon from '@lucide/svelte/icons/sticky-note';
	import XIcon from '@lucide/svelte/icons/x';
	import type { Snippet } from 'svelte';
	import * as Collapsible from '../../ui/collapsible/index.js';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import { Money } from '../../ui/money/index.js';
	import { formatDate } from '../../../dates.js';
	import { dueLabel, isOpen, isOverdue, byUrgency } from '../tasks/index.js';
	import { REQUEST_ACTION_LABELS } from '../documents/index.js';
	import type { TaskRow } from '../tasks/index.js';
	import type { ApiRequest } from '../documents/index.js';
	// `ThreadMessage` is the `generics=` constraint above and `RailPerson` is a prop's type;
	// svelte-eslint-parser does not look inside that attribute, so the rule has to be told.
	// eslint-disable-next-line @typescript-eslint/no-unused-vars
	import type { RailAccount, RailClient, RailNote, RailPerson, ThreadMessage } from './messages.js';

	let {
		accounts = [],
		client = null,
		clientHref = null,
		notes = [],
		onAddNote,
		onDeleteNote,
		people,
		peopleNote,
		requests = [],
		tasks = [],
		showTasks = true,
		links,
		label = 'Client details'
	}: {
		/** WHO THIS CONVERSATION IS WITH, at the head of the rail: the question the inbox is holding
		 *  while it reads a thread, and the one thing the rail never said.
		 *
		 *  Absent on the client RECORD, deliberately — there the client is the page, their name is
		 *  already on the header and the thread, and a "View profile" button pointing at the page you
		 *  are standing on is furniture. Absent too for a GROUP, which is several clients, and for a
		 *  reader without `contacts:read`. */
		client?: RailClient | null;
		/** The client's own record. Absent ⇒ the block states the name without offering a way to it,
		 *  which is what a mount with no client route (the admin book) needs. */
		clientHref?: string | null;
		/** What the client holds. Absent for a GROUP and for a reader without `contacts:read`, the
		 *  same two cases the client block is absent for. */
		accounts?: RailAccount[];
		/** The recent notes the firm has kept on this client — the whole history is on the record. */
		notes?: RailNote[];
		/** Write a note from here. Resolves with an error message, or null when it landed. ABSENT ⇒
		 *  no composer: a read-only mount, or a reader without `contacts:write`, must not be shown
		 *  a box whose submit can only fail. */
		onAddNote?: (body: string) => Promise<string | null>;
		/** Withdraw a note from here. Resolves with an error message, or null when it went. The
		 *  API restricts it to the CALLER'S OWN notes, so a colleague's answers 404 and the rail
		 *  reports that rather than guessing authorship from a display name. Absent ⇒ no delete,
		 *  the same way an absent `onAddNote` means no composer. */
		onDeleteNote?: (uid: string) => Promise<string | null>;
		people: RailPerson[];
		requests?: ApiRequest[];
		tasks?: TaskRow[];
		/** Keep journey task data available to mounts that use it without exposing Tasks as a
		 * standalone platform feature. */
		showTasks?: boolean;
		links?: {
			request: (uid: string) => string;
			task: (uid: string) => string;
			/** The account's own page. Optional, like the other two: a mount with no `/accounts`
			 *  tree states the row plainly rather than offering a dead link. */
			account?: (uid: string) => string;
		};
		/** Shown under a People list that names nobody but the client — "No adviser is assigned to
		 *  this client." on a 1:1 thread. Absent for a group, whose members always exist. */
		peopleNote?: string;
		label?: string;
	} = $props();

	/** The facts worth stating beside a thread, in the order an adviser would ask them, and EVERY
	 *  one of them — an em dash where there is no answer, exactly as the record states them. A
	 *  field that vanishes when it is empty is a field nobody knows is missing, and "we have no
	 *  phone number for them" is itself an answer an adviser needs mid-conversation. */
	const mailto = (v: string | null | undefined) => (v ? `mailto:${v}` : null);
	/** Stripped of everything a dialler cannot take — spaces and brackets are for reading. */
	const tel = (v: string | null | undefined) => (v ? `tel:${v.replace(/[^+\d]/g, '')}` : null);
	/** A LIST, whatever arrived. `GET /documents/requests` and `GET /crm/contacts` answer two
	 *  shapes and pick by whether the caller sent a `limit` — a bare array, or `{items, total}`.
	 *  A mount that sent one handed this panel an object, `requests.filter(...)` threw, and the
	 *  whole record page went to a 500 over a side panel. A panel that cannot read its own answer
	 *  shows nothing; it does not take the conversation down with it. */
	const listOf = <T,>(value: T[] | { items?: T[] } | null | undefined): T[] =>
		Array.isArray(value) ? value : Array.isArray(value?.items) ? value.items : [];
	// Only what the client still owes. A fulfilled or cancelled request is history and belongs on
	// the Documents section.
	const openRequests = $derived(listOf(requests).filter((r) => r.status === 'pending'));
	// `isOpen` is open OR in progress — a task somebody has started is still outstanding.
	const openTasks = $derived(
		showTasks ? [...listOf(tasks).filter(isOpen)].sort((a, b) => byUrgency(a, b)) : []
	);
	const taskCount = $derived(openRequests.length + openTasks.length);

	/** WHAT OPENS ITSELF. Accounts and Notes answer "who is this and what is going on with them",
	 *  which is why the rail is read at all; Tasks and People are checked deliberately, so they
	 *  start closed with their count on the band and cost one click when they are wanted. None of
	 *  it is persisted — a fold is a glance, not a setting. */
	let open = $state({ accounts: true, notes: true, tasks: false, people: false });
	const toggle = (k: keyof typeof open) => () => (open[k] = !open[k]);

	/** THREE, then the rest on request. A client with eight wrappers pushed Notes and everything
	 *  under it off the bottom, and the first three answer "roughly what do they hold" on their
	 *  own. */
	const ACCOUNTS_SHOWN = 3;
	let allAccounts = $state(false);
	const shownAccounts = $derived(allAccounts ? accounts : accounts.slice(0, ACCOUNTS_SHOWN));

	let draft = $state('');
	let saving = $state(false);
	let noteError = $state('');

	/** WHO THE BOX IS ABOUT. The inbox keeps ONE rail mounted and swaps the client under it, so a
	 *  half-typed note stayed in the box across a selection change and the next save wrote it
	 *  onto whoever was selected by then. `clientHref` carries the uid and is the one identity
	 *  here that cannot collide; the name is the fallback for a mount with no client route. */
	const noteSubject = $derived(client ? (clientHref ?? client.name ?? null) : null);
	let draftSubject: string | null = null;
	$effect(() => {
		const subject = noteSubject;
		if (subject === draftSubject) return;
		draftSubject = subject;
		draft = '';
		noteError = '';
	});
	async function submitNote(event: SubmitEvent) {
		event.preventDefault();
		const body = draft.trim();
		if (!body || saving || !onAddNote) return;
		saving = true;
		noteError = '';
		const failure = await onAddNote(body);
		saving = false;
		if (failure) {
			noteError = failure;
			return;
		}
		draft = '';
	}

	/** The note being removed, so a slow round trip cannot be clicked twice. */
	let deleting = $state<string | null>(null);
	async function removeNote(uid: string) {
		if (deleting || !onDeleteNote) return;
		deleting = uid;
		noteError = '';
		const failure = await onDeleteNote(uid);
		deleting = null;
		if (failure) noteError = failure;
	}

	const facts = $derived(
		client
			? [
					// How to reach them first: that is what a panel beside a live conversation is for.
					// TYPE is gone — "Individual" next to a person's own name and face says nothing a
					// reader did not already know.
					{ label: 'Email', value: client.email ?? null, mono: false, href: mailto(client.email) },
					{ label: 'Phone', value: client.phone ?? null, mono: false, href: tel(client.phone) },
					{
						label: 'DOB',
						value: client.dob ? formatDate(client.dob) : null,
						mono: false,
						href: null
					},
					{ label: 'Address', value: client.address ?? null, mono: false, href: null },
					{ label: 'Service', value: client.serviceType ?? null, mono: false, href: null }
				]
			: []
	);
</script>

<!-- A DIVIDER, not a border box: one hairline against the thread, on the top when stacked and on
     the left when beside it. `lg:shrink-0`, not `shrink-0`, because beside the thread the rail
     keeps its 20rem, but stacked under it inside a fixed-height column it has to be allowed to
     shrink or a long list grows past the bottom of the page instead of scrolling inside itself.
     Sits on the page background, not `bg-card`, so Messages matches the rest of the record.

     A COLUMN, not one scroller: who you are talking to is pinned at the top and only the bands
     below it move. Scrolling to a note or the fourth account must never scroll away the name,
     email and phone of the person the whole panel is about. -->
<aside
	class={cn('flex min-h-0 w-full flex-col border-t lg:w-80 lg:shrink-0 lg:border-t-0 lg:border-l')}
	aria-label={label}
>
	<!-- THE CLIENT, and it neither folds nor scrolls. Only this block depends on `client`: the rest
	     of the rail must render without one, because a GROUP has no single client and the record's
	     Messages tab passes none (there the client is the page). -->
	{#if client}
		<section class="shrink-0 pb-2">{@render clientPanel()}</section>
	{/if}
	<!-- NO RULES BETWEEN THE BANDS. Each one already announces itself with a bold heading and a
	     chevron, so a hairline under every heading and between every row turned a short panel into
	     a ledger. Space does the separating. -->
	<div class="min-h-0 flex-1 overflow-y-auto">
		{#if accounts.length > 0}
			{@render band(
				'Accounts',
				accountsList,
				open.accounts,
				toggle('accounts'),
				String(accounts.length)
			)}
		{/if}
		{@render band('Notes', notesList, open.notes, toggle('notes'))}
		{#if taskCount > 0}
			{@render band(
				showTasks ? 'Tasks' : 'Requests',
				tasksPanel,
				open.tasks,
				toggle('tasks'),
				String(taskCount)
			)}
		{/if}
		<!-- PEOPLE AT THE FOOT. Who else is on a conversation is the thing checked least often of
		     everything here, so it sits below the client's own facts and their work. -->
		{@render band('People', peopleList, open.people, toggle('people'), String(people.length))}
	</div>
</aside>

{#snippet band(
	title: string,
	content: Snippet,
	isOpen: boolean,
	onToggle: () => void,
	count?: string
)}
	<Collapsible.Root open={isOpen} onOpenChange={onToggle} class="group/sec">
		<section>
			<Collapsible.Trigger
				class="hover:bg-muted/40 focus-visible:ring-ring/50 flex w-full items-center gap-2 px-4 py-2.5 text-[0.8125rem] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-inset"
			>
				<ChevronDownIcon
					class="text-muted-foreground size-4 shrink-0 transition-transform duration-200 group-data-[state=closed]/sec:-rotate-90"
				/>
				{title}
				{#if count}
					<span class="text-muted-foreground text-xs font-medium tabular-nums">{count}</span>
				{/if}
			</Collapsible.Trigger>
			<Collapsible.Content>
				{@render content()}
			</Collapsible.Content>
		</section>
	</Collapsible.Root>
{/snippet}

{#snippet clientPanel()}
	<div class="flex items-center gap-2.5 px-4 pt-3 pb-2.5">
		<ClientAvatar name={client?.name ?? 'Unknown'} kind={client?.kind} size="sm" />
		<!-- THE NAME IS THE LINK. A separate "View profile" beside it was two things to read for one
		     destination, and the name is what a reader reaches for anyway. Without an href it is
		     plain text — a mount with no client route must not offer a dead link. -->
		{#if clientHref}
			<a
				href={clientHref}
				class="hover:text-brand min-w-0 flex-1 truncate text-sm font-semibold hover:underline"
			>
				{client?.name ?? 'Unknown'}
			</a>
		{:else}
			<span class="min-w-0 flex-1 truncate text-sm font-semibold">{client?.name ?? 'Unknown'}</span>
		{/if}
	</div>
	<!-- EMAIL AND PHONE LIVE HERE, not under the name: they are facts like the rest, and a mailto
	     is more use in a labelled row than as a caption.

	     TWO COLUMNS, both reading from the left. Ranged right, every value started at a different
	     place down the panel, so reading three facts meant three separate hunts for where the
	     answer began — and an em dash for a missing one landed alone on the far edge. A fixed
	     label column gives the values one edge to start from. -->
	<dl class="grid grid-cols-[5.25rem_1fr] gap-x-3 gap-y-1.5 px-4 pb-3">
		{#each facts as fact (fact.label)}
			<dt class="text-muted-foreground truncate text-xs">{fact.label}</dt>
			<dd
				class={cn(
					'min-w-0 text-xs font-medium',
					fact.label === 'Address' ? 'break-words' : 'truncate',
					fact.mono && fact.value && 'font-mono',
					!fact.value && 'text-muted-foreground'
				)}
				title={fact.value ?? undefined}
			>
				{#if !fact.value}
					—
				{:else if fact.href}
					<a href={fact.href} class="hover:text-brand hover:underline">{fact.value}</a>
				{:else}
					{fact.value}
				{/if}
			</dd>
		{/each}
	</dl>
{/snippet}

{#snippet accountsList()}
	<ul class="px-4">
		{#each shownAccounts as account (account.uid)}
			<li>
				<!-- THE ROW IS THE ROAD TO THE ACCOUNT. "What is this conversation about" is usually a
				     wrapper, and the next question is always the wrapper itself — so the summary is
				     the way there rather than a thing to read and then go looking for. -->
				{#if links?.account}
					<a
						href={links.account(account.uid)}
						class="hover:bg-muted/60 -mx-2 block rounded-lg px-2"
					>
						{@render accountRow(account)}
					</a>
				{:else}
					{@render accountRow(account)}
				{/if}
			</li>
		{/each}
	</ul>
	{#if accounts.length > ACCOUNTS_SHOWN}
		<button
			type="button"
			onclick={() => (allAccounts = !allAccounts)}
			class="text-muted-foreground hover:text-foreground w-full px-4 pt-1 pb-2 text-center text-xs font-medium"
		>
			{allAccounts ? 'Show less' : `Show ${accounts.length - ACCOUNTS_SHOWN} more`}
		</button>
	{/if}
{/snippet}

<!-- An account's line, shared by the linked and unlinked forms so the two cannot drift. -->
{#snippet accountRow(account: RailAccount)}
	<!-- TWO COLUMNS READING FROM THE LEFT, the same shape as the facts above: every value starts
	     at one edge, so three accounts are one column to read down rather than three amounts
	     ranged against the far side. -->
	<span class="grid grid-cols-[1fr_7rem] items-baseline gap-x-3 py-2">
		<!-- THE NAME ALONE. The provider's reference is a filing code for talking to a custodian,
		     not something read beside a conversation, and a mono line under every row made a
		     three-account summary twice as tall as the facts above it. It is on the account's own
		     page, which this row now leads to. -->
		<span class="min-w-0 truncate text-xs font-medium">
			{account.name ?? account.productType ?? 'Account'}
		</span>
		<span class="min-w-0">
			<span class="block text-xs font-medium tabular-nums">
				{#if account.value == null}—{:else}<Money amount={account.value} />{/if}
			</span>
			<!-- GROWTH under the balance: "is this going the right way" is the second question anyone
			     asks of a value, and it is the one figure here where up and down are the fact being
			     read — so it takes the directional tint. -->
			{#if account.growth != null && account.growth !== 0}
				<span class="block text-[11px] font-medium tabular-nums">
					<Money amount={account.growth} signed tone="directional" />
				</span>
			{/if}
		</span>
	</span>
{/snippet}

{#snippet tasksPanel()}
	<!-- TWO LISTS, because they are two different people's work: what the CLIENT owes (open
	     document requests) and what the FIRM owes about them. Each is absent rather than empty. -->
	<div class="space-y-3 px-2 py-2">
		{#if openRequests.length > 0}
			<div>
				<h3 class="text-muted-foreground px-2 pb-1 text-[11px] font-semibold">
					{showTasks ? 'Pending client tasks' : 'Pending requests'}
				</h3>
				<ul class="space-y-1">
					{#each openRequests as request (request.uid)}
						<li>
							{#if links}
								<a
									href={links.request(request.uid)}
									class="hover:bg-muted block rounded-lg px-2 py-1.5"
								>
									{@render requestBody(request)}
								</a>
							{:else}
								<div class="px-2 py-1.5">{@render requestBody(request)}</div>
							{/if}
						</li>
					{/each}
				</ul>
			</div>
		{/if}
		{#if openTasks.length > 0}
			<div>
				<h3 class="text-muted-foreground px-2 pb-1 text-[11px] font-semibold">Open tasks</h3>
				<ul class="space-y-1">
					{#each openTasks as task (task.uid)}
						<li>
							{#if links}
								<a href={links.task(task.uid)} class="hover:bg-muted block rounded-lg px-2 py-1.5">
									{@render taskBody(task)}
								</a>
							{:else}
								<div class="px-2 py-1.5">{@render taskBody(task)}</div>
							{/if}
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	</div>
{/snippet}

{#snippet notesList()}
	<!-- The RECENT ones, with a box to add another. The record holds the whole history; this
	     answers "what do we already know about them" without leaving the conversation. -->
	{#if onAddNote}
		<!-- WRITTEN WHERE IT WAS HEARD. "They said on the phone they are retiring in March" is
		     learned in a conversation, and a note that needs a trip to the record and back is the
		     note that never gets written. Ctrl+Enter to save, the same key the composer below the
		     thread takes.

		     THE RECORD'S OWN NOTES CARD, to the component: `NoteComposer` is the one field a note
		     is written in, here, on the record and on a transfer, so the darker field and the
		     ghost icon cannot drift between them. A separate button beneath spent a whole row of a
		     20rem column on a control the box already implies. -->
		<form onsubmit={submitNote} class="px-4 pt-3 pb-2">
			<NoteComposer
				bind:value={draft}
				placeholder="Add a note about this client…"
				pending={saving}
				onkeydown={(event) => {
					if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
						event.preventDefault();
						if (event.currentTarget instanceof HTMLTextAreaElement) {
							event.currentTarget.form?.requestSubmit();
						}
					}
				}}
			/>
		</form>
	{/if}
	{#if noteError}
		<p class="text-destructive px-4 pb-2 text-xs" role="status">{noteError}</p>
	{/if}
	<!-- ONE OR THE OTHER, never both. An empty state under a box whose placeholder already reads
	     "Add a note about this client…" says the same thing twice and costs the band a whole panel
	     to do it. The state is still stated to a reader who cannot write one, because to them
	     "no notes" is the only answer this band has. -->
	{#if notes.length === 0 && !onAddNote}
		<div class="px-4 pt-1 pb-3">
			<EmptyState
				compact
				icon={StickyNoteIcon}
				title="No notes yet"
				description="Notes the firm keeps on this client appear here."
			/>
		</div>
	{:else if notes.length > 0}
		<!-- THE RECORD'S OWN NOTES LIST, to the row: a hairline between notes, the body over its
		     author line, and the delete in the top-right corner on hover or focus only — a cross on
		     every row of a list invites a mis-click, where an adviser withdrawing their own note
		     knows which one they mean. Same rules here so the two readings of one note agree. -->
		<ul class="space-y-3 px-4 pb-3">
			{#each notes as note (note.uid)}
				<li class="group/note relative border-b pb-3 last:border-0 last:pb-0">
					{#if onDeleteNote}
						<Button
							type="button"
							variant="ghost"
							size="icon-sm"
							class="text-muted-foreground hover:text-destructive absolute -top-1.5 right-0 opacity-0 transition-opacity group-focus-within/note:opacity-100 group-hover/note:opacity-100"
							title="Delete this note"
							aria-label="Delete this note"
							disabled={deleting === note.uid}
							onclick={() => removeNote(note.uid)}
						>
							<XIcon class="size-3.5" />
						</Button>
					{/if}
					<p
						class={cn(
							'text-xs leading-relaxed break-words whitespace-pre-wrap',
							onDeleteNote && 'pr-7'
						)}
					>
						{note.body}
					</p>
					<p class="text-muted-foreground mt-1 text-xs">
						{note.author ?? 'Someone'} · {formatDate(note.createdAt)}
					</p>
				</li>
			{/each}
		</ul>
	{/if}
{/snippet}

{#snippet peopleList()}
	<ul class="space-y-1 p-2">
		{#each people as person, i (`${person.name ?? 'unnamed'}-${i}`)}
			<li class="flex items-center gap-3 rounded-lg px-2 py-1.5">
				{#if person.isClient}
					<ClientAvatar name={person.name ?? 'Unknown'} kind={client?.kind} size="sm" />
				{:else}
					<PersonAvatar name={person.name ?? 'Unknown'} size="sm" />
				{/if}
				<span class="min-w-0 flex-1">
					<span class="block truncate text-sm font-medium">{person.name ?? 'Unnamed'}</span>
					<span class="text-muted-foreground block truncate text-xs">{person.role ?? ''}</span>
				</span>
			</li>
		{/each}
		{#if peopleNote && people.length <= 1}
			<li class="text-muted-foreground px-2 py-1.5 text-xs">{peopleNote}</li>
		{/if}
	</ul>
{/snippet}

<!-- A request's line, shared by the linked and unlinked forms so the two cannot drift. -->
{#snippet requestBody(request: ApiRequest)}
	<span class="block truncate text-xs font-medium">
		{request.items[0]?.title ?? 'Request'}{request.items.length > 1
			? ` + ${request.items.length - 1} more`
			: ''}
	</span>
	<span class="text-muted-foreground block truncate text-[11px]">
		{request.items.map((i) => REQUEST_ACTION_LABELS[i.action]).join(' · ')}
	</span>
{/snippet}

{#snippet taskBody(task: TaskRow)}
	<span class="flex items-center gap-2">
		<span class="min-w-0 flex-1 truncate text-xs font-medium">{task.title}</span>
		{#if isOverdue(task)}
			<Badge variant="destructive" class="shrink-0">Overdue</Badge>
		{/if}
	</span>
	{#if dueLabel(task)}
		<span class="text-muted-foreground block truncate text-[11px]">{dueLabel(task)}</span>
	{/if}
{/snippet}
