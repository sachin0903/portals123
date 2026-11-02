<script lang="ts">
	// The detail of one action event: the CHANGES-first delta, the resolved entity rows, then the
	// machine facts. The pure read logic lives in event-detail.ts.
	//
	// TWO READINGS, one component (`variant`). Beside a list (`pane`, the feed) it must introduce its
	// own event. Under its own row (`inline`, the stream) all of that is one line above, so it drops
	// the five the row states — actor name, sentence, outcome, time, raw event type.
	//
	// AN ENTITY'S NAME IS ITS LINK. The "Open …" button restated a kind the label beside it already
	// gave, and forced the target to be plain text so the button could carry the link — so the one
	// record a reader most wanted was the one that did not look clickable. No raw-JSON block and no
	// correlation id; both are still on the event for whoever queries the stream.

	import { cn } from '../../utils.js';
	import { LINK_RECORD } from '../../text-shell.js';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import {
		entityKindLabel,
		methodLabel,
		methodRoute,
		statusLabel,
		statusTone,
		timestampParts
	} from './describe.js';
	import type { ActionEntity, ActionEvent } from './describe.js';
	import {
		bodyFields,
		changeRows,
		currentIds,
		entitiesOf,
		headline,
		legacyPermissionsCount,
		permissionsDiffOf,
		previousIds,
		queryParams,
		detailRows,
		residualPathParams
	} from './event-detail.js';
	import { capText } from './format-value.js';

	let {
		event,
		links,
		entityHref,
		variant = 'pane',
		now = new Date()
	}: {
		event: ActionEvent;
		/** Turns a target id chip (a path param, e.g. `contact_id`) into a link into the host app's
		 *  own routes — return an href, or null for "no route for this id here". */
		links?: (key: string, value: unknown) => string | null;
		/** The entity-row counterpart of `links`: turns a RESOLVED entity into a link (given the
		 *  whole event, so a cross-tenant surface can scope the route). */
		entityHref?: (event: ActionEvent, entity: ActionEntity) => string | null;
		/**
		 * WHERE this detail is being read, which decides what it may leave out. `pane` (the feed) puts
		 * it BESIDE a list, so it has to introduce its own event. `inline` (the stream) opens it UNDER
		 * its own row, where the actor, the sentence, the outcome, the time and the raw event type are
		 * one line above — so it drops those five and keeps what is genuinely new.
		 */
		variant?: 'pane' | 'inline';
		now?: Date;
	} = $props();

	const inline = $derived(variant === 'inline');

	const led = $derived(headline(event));
	const details = $derived(detailRows(event));
	const stamp = $derived(timestampParts(event.created_at, now));
	const diff = $derived(permissionsDiffOf(event));
	const legacyPerms = $derived(legacyPermissionsCount(event));
	const changes = $derived(changeRows(event));
	const body = $derived(bodyFields(event));
	/**
	 * A body field that is PROSE — a note, a message, a reason — gets the whole row of the inline
	 * grid, with the other wide facts at the bottom. In a quarter-width cell a long note stood as
	 * a column of broken words beside the actor's email, and where it carried an unbroken run
	 * (a URL, a reference) it ran straight under the next cell.
	 */
	const isProse = ([, value]: [string, string]) => value.length > 80 || value.includes('\n');
	const shortBody = $derived(body.filter((field) => !isProse(field)));
	const proseBody = $derived(body.filter(isProse));
	const ents = $derived(entitiesOf(event));
	const residual = $derived(residualPathParams(event));
	const wasIds = $derived(previousIds(event));
	const nowIds = $derived(currentIds(event));

	/**
	 * The path params the panel states in their own right — the ones no entity row named. Where
	 * entities resolved this is `residual`; where none did it is all of them, because the raw id is
	 * then the only thing the stream holds about what was touched.
	 */
	const residualParams = $derived(
		ents.length > 0 ? residual : Object.entries(event.path_params ?? {})
	);
</script>

{#snippet factRow(label: string)}
	<!-- THE SAME SHAPE as the entity rows above: an uppercase pill for the kind of fact, then the
	     fact. A definition list with a 6rem label column read as a form on a screen whose every
	     other line is a pill and a value, so the eye had two grammars to follow down one pane.
	     `pane` only — see `factGrid` for the inline reading and why the two differ. -->
	<span
		class="border-border bg-muted text-muted-foreground shrink-0 rounded-full border px-2 py-0.5 text-[10px] tracking-wide uppercase"
		>{label}</span
	>
{/snippet}

{#snippet fact(label: string, title?: string)}
	<dt class="text-muted-foreground text-[11px]" {title}>{label}</dt>
{/snippet}

{#snippet paramChips(entries: [string, unknown][], linkable: boolean = false)}
	<span class="flex flex-wrap gap-1.5">
		{#each entries as [key, value] (key)}
			{@const href = linkable ? (links?.(key, value) ?? null) : null}
			<span
				class="bg-background inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px]"
			>
				<span class="text-muted-foreground">{key}</span>
				{#if href}
					<a class={cn('font-mono', LINK_RECORD)} {href}>{String(value)}</a>
				{:else}
					<code
						class={cn(
							'font-mono',
							String(value) === '[redacted]' && 'text-muted-foreground italic'
						)}>{String(value)}</code
					>
				{/if}
			</span>
		{/each}
	</span>
{/snippet}

<!-- INLINE carries no vertical padding of its own: the expanded table cell already provides it
     (`data-table.svelte`'s `py-4`), and the two together left a band of empty space above and below
     a three-item fact grid. A PANE is a standalone panel and pads itself.
     INLINE also resets `white-space`: the host `Table.Cell` is `whitespace-nowrap`, which every
     `dd` below inherited — so a note body never wrapped at its spaces and ran straight under the
     next column, whatever `break-words` said. -->
<div class={cn('space-y-3', inline ? 'px-5 whitespace-normal' : 'p-5')}>
	{#if !inline}
		<!-- The event itself: the actor-led line + status. PANE ONLY — inline this is the row the
		     panel just opened out of, word for word, and it filled the top of every expansion with
		     the sentence the reader had their finger on. -->
		<div class="flex flex-wrap items-center gap-2">
			<span class="text-sm"><span class="font-medium">{led.actor}</span>{led.rest}</span>
			<!-- The status as a WORD. The code itself rides in the title for whoever wants it — a bare
			     200 beside every row says "nothing went wrong" in a dialect most readers of this log do
			     not speak. -->
			<StatusBadge tone={statusTone(event.status)}>
				<span title="HTTP {event.status}">{statusLabel(event.status)}</span>
			</StatusBadge>
		</div>
	{/if}

	{#if !inline}
		<!-- CHANGES first: the permission delta, the old → new rows, the resolved entity rows.
		     PANE ONLY. Inline every one of these is a cell of the one grid below: the entity was a
		     pill row and a name while Actor and Operation were labelled facts, so one panel asked
		     the eye to read three grammars down it. -->
		<div class="space-y-2 text-xs">
			{#if diff}
				<!-- Only the delta, never the full list (the full list lives in Details). -->
				<div class="flex flex-wrap items-center gap-1.5">
					<span class="text-muted-foreground">Permissions</span>
					{#each diff.added as slug (slug)}
						<span
							class="bg-background text-success-strong inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px]"
							>+{slug}</span
						>
					{/each}
					{#each diff.removed as slug (slug)}
						<span
							class="bg-background text-destructive inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px]"
							>−{slug}</span
						>
					{/each}
					{#if diff.added.length === 0 && diff.removed.length === 0}
						<span class="text-muted-foreground italic">no changes</span>
					{/if}
				</div>
			{:else if legacyPerms !== null}
				<!-- No previous to diff against: a COUNT, never the dump (engineers get it in Details). -->
				<div class="flex flex-wrap items-center gap-1.5">
					<span class="text-muted-foreground">Permissions</span>
					<span
						class="bg-background inline-flex items-center rounded-md border px-2 py-0.5 text-[11px]"
						>{legacyPerms} permission{legacyPerms === 1 ? '' : 's'} set</span
					>
				</div>
			{/if}
			{#each changes as change (change.label)}
				<div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
					<span class="text-muted-foreground">{change.label}</span>
					{#if change.from !== null && change.to !== null}
						<span class="font-medium break-all">{capText(change.from)}</span>
						<span class="text-muted-foreground" aria-hidden="true">→</span>
						<span class="font-medium break-all">{capText(change.to)}</span>
					{:else if change.to !== null}
						<span class="font-medium break-all">{capText(change.to)}</span>
					{:else}
						<span class="text-muted-foreground">was</span>
						<span class="font-medium break-all">{capText(change.from)}</span>
					{/if}
				</div>
			{/each}

			{#if ents.length > 0}
				<!-- Resolved entities: kind, CURRENT display name, mono id. A null label means the entity
			     is deleted/unknown — the bare id is shown, which is honest. An id that names only the
			     BEFORE state wears a quiet "previous" pill. -->
				<div class="flex flex-col gap-1">
					{#each ents as ent (`${ent.kind}:${ent.id}`)}
						{@const href = entityHref?.(event, ent) ?? null}
						<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5" title={ent.id}>
							<span
								class="border-border bg-muted text-muted-foreground rounded-full border px-2 py-0.5 text-[10px] tracking-wide uppercase"
								>{entityKindLabel(ent.kind)}</span
							>
							{#if ent.label}
								{#if href}
									<a class={cn('font-medium', LINK_RECORD)} {href}>{ent.label}</a>
								{:else}
									<span class="font-medium">{ent.label}</span>
								{/if}
								{#if !inline}
									<!-- The id BESIDE the name is pane-only. Inline the panel opens under its own
								     row in a full-width table, where a 32-character uid is the widest thing on
								     the screen and says nothing the name has not already said. It stays on the
								     row's `title`, so the one reader matching a support ticket still has it. -->
									<code class="text-muted-foreground font-mono">{ent.id}</code>
								{/if}
							{:else if href}
								<a class={cn('font-medium', LINK_RECORD)} {href}
									>{inline ? 'Open the record' : ent.id}</a
								>
							{:else if inline}
								<!-- Nothing could name it: the record is deleted, or its kind has no resolver yet.
							     Inline that is said in WORDS — printing the uid instead tells the reader the
							     one thing they cannot use, and it is on the row's `title` either way. -->
								<span class="text-muted-foreground italic">no longer available</span>
							{:else}
								<code class="font-mono">{ent.id}</code>
							{/if}
							{#if wasIds.has(ent.id) && !nowIds.has(ent.id)}
								<span
									class="border-border text-muted-foreground rounded-full border px-1.5 py-0.5 text-[10px] tracking-wide uppercase"
									>previous</span
								>
							{/if}
						</span>
					{/each}
					{#if residual.length > 0}
						{@render paramChips(residual, true)}
					{/if}
				</div>
			{:else if Object.keys(event.path_params ?? {}).length > 0}
				<div>{@render paramChips(Object.entries(event.path_params), true)}</div>
			{/if}
		</div>
	{/if}

	<!-- ONE LIST. It was two collapsibles — "Details" for when/who, "Trace" for the machine's own
	     facts — which is one section heading per two rows and two clicks to read five facts. The
	     ORDER carries the distinction instead: what a person reads an audit log for comes first,
	     and the type code and route follow. Nothing is folded, because nothing here is long enough
	     to be worth hiding.
	     The CORRELATION ID is not here. It is a request-tracing id for an engineer chasing a
	     support ticket, and it spent a whole row plus a copy button saying nothing to anyone
	     reading the log. It is still on the event (`metadata.correlation_id`) for whoever queries
	     it. -->
	{#if inline}
		<!-- THE INLINE FACT SHEET. The pane's pill-per-line reading is right beside a list, where
		     every line is a pill and a value and a 6rem label column would read as a form. Under its
		     own row in a FULL-WIDTH table it was the opposite problem: six facts stacked in one
		     narrow column down the left, most of them repeating the row, with the rest of the screen
		     empty. A grid states them side by side and the panel stops being taller than the event.
		     Same shape as the account record's info grid, which is the house pattern for a fact
		     sheet. -->
		<dl class="grid grid-cols-2 gap-x-6 gap-y-3 text-xs lg:grid-cols-4">
			<!-- WHAT IT WAS DONE TO, FIRST AND BY NAME. A reader opens an event to find out which
			     record it touched, so it leads — and it is a labelled fact like every other, rather
			     than the pill-and-name row it used to be beside a column of labelled facts. The uid
			     rides on the label's `title`: it is how the row is keyed, not how a person knows the
			     record, and where nothing could name it the residual param below states it outright. -->
			{#each ents as ent (`${ent.kind}:${ent.id}`)}
				{@const href = entityHref?.(event, ent) ?? null}
				<div class="min-w-0">
					{@render fact(entityKindLabel(ent.kind), ent.id)}
					<dd class="flex min-w-0 flex-wrap items-center gap-2">
						{#if ent.label}
							{#if href}
								<a class={cn('min-w-0 truncate font-medium', LINK_RECORD)} {href}>{ent.label}</a>
							{:else}
								<span class="min-w-0 truncate font-medium" title={ent.label}>{ent.label}</span>
							{/if}
						{:else if href}
							<a class={cn('min-w-0 truncate font-mono', LINK_RECORD)} {href}>{ent.id}</a>
						{:else}
							<!-- Nothing could name it (deleted, or a kind with no resolver). The id IS the
							     honest answer, and it is the detail somebody would need to find the row. -->
							<code class="min-w-0 truncate font-mono" title={ent.id}>{ent.id}</code>
						{/if}
						{#if wasIds.has(ent.id) && !nowIds.has(ent.id)}
							<span
								class="border-border text-muted-foreground shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] tracking-wide uppercase"
								>previous</span
							>
						{/if}
					</dd>
				</div>
			{/each}
			<!-- The path params NO entity row named. Kept, never tidied away: with the route above
			     them they are what it would take to replay the request, and where the record could
			     not be resolved they are the only thing the stream holds about it. `residual` is
			     already the set an entity row does not cover, so a named record shows no id here. -->
			{#each residualParams as [key, value] (key)}
				{@const href = links?.(key, value) ?? null}
				<div class="min-w-0">
					{@render fact(key)}
					<dd class="min-w-0 truncate" title={String(value)}>
						{#if href}
							<a class={cn('font-mono', LINK_RECORD)} {href}>{String(value)}</a>
						{:else}
							<code
								class={cn(
									'font-mono',
									String(value) === '[redacted]' && 'text-muted-foreground italic'
								)}>{String(value)}</code
							>
						{/if}
					</dd>
				</div>
			{/each}
			<!-- WHAT THE SAVE MOVED, before the request that asked for it. An update's reader wants
			     the old value beside the new one, and `metadata.changes` is the only capture that
			     holds both — so it leads, and `bodyFields` drops whatever it has already covered. -->
			{#each changes as change (change.label)}
				<div class="min-w-0">
					{@render fact(change.label)}
					<dd class="font-medium break-words">
						{#if change.from !== null && change.to !== null}
							{capText(change.from)}
							<span class="text-muted-foreground" aria-hidden="true">→</span>
							{capText(change.to)}
						{:else if change.to !== null}
							{capText(change.to)}
						{:else}
							<span class="text-muted-foreground">was</span>
							{capText(change.from)}
						{/if}
					</dd>
				</div>
			{/each}
			<!-- THE REQUEST ITSELF (`metadata.body`). An act whose content is free text or a form's
			     answers cannot be read from a handful of captured ids, so the body is recorded and
			     stated here field by field — ordinary labelled facts, not a blob to parse. -->
			{#each shortBody as [label, value] (label)}
				<div class="min-w-0">
					{@render fact(label)}
					<dd class="font-medium break-words">{value}</dd>
				</div>
			{/each}
			{#if event.actor_email}
				<!-- The EMAIL only: the row above already names the person, and this is the part of
				     their identity it does not carry. -->
				<div class="min-w-0">
					{@render fact('Actor', event.actor_sub ?? undefined)}
					<dd class="truncate font-medium" title={event.actor_email}>{event.actor_email}</dd>
				</div>
			{/if}
			{#each details as [label, value] (label)}
				<div class="min-w-0">
					{@render fact(label)}
					<dd class="font-medium break-words">{value}</dd>
				</div>
			{/each}
			<div class="min-w-0">
				{@render fact('Operation')}
				<dd class="truncate" title={methodRoute(event)}>
					<span class="font-medium">{methodLabel(event.method)}</span>
					<code class="text-muted-foreground ml-1 font-mono">{event.route}</code>
				</dd>
			</div>
			<!-- THE WIDE ONES LAST. A `col-span` item in the middle of the grid breaks the row it lands
			     on, so whatever came before it is stranded alone with empty cells beside it - `slug` on a
			     line of its own above the permission chips. Short facts pack first; the ones that need
			     the whole row take it at the bottom. -->
			{#each proseBody as [label, value] (label)}
				<div class="col-span-2 min-w-0 lg:col-span-4">
					{@render fact(label)}
					<dd class="font-medium break-words whitespace-pre-wrap [overflow-wrap:anywhere]">
						{value}
					</dd>
				</div>
			{/each}
			{#if diff}
				<!-- Spans the row: a delta is a list of chips, not a one-line value, and a quarter of
				     the width wraps it into a column of confetti. -->
				<div class="col-span-2 min-w-0 lg:col-span-4">
					{@render fact('Permissions')}
					<dd class="flex flex-wrap items-center gap-1.5 pt-0.5">
						{#each diff.added as slug (slug)}
							<span
								class="bg-background text-success-strong inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px]"
								>+{slug}</span
							>
						{/each}
						{#each diff.removed as slug (slug)}
							<span
								class="bg-background text-destructive inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px]"
								>−{slug}</span
							>
						{/each}
						{#if diff.added.length === 0 && diff.removed.length === 0}
							<span class="text-muted-foreground italic">no changes</span>
						{/if}
					</dd>
				</div>
			{:else if legacyPerms !== null}
				<div class="min-w-0">
					{@render fact('Permissions')}
					<dd class="font-medium">{legacyPerms} permission{legacyPerms === 1 ? '' : 's'} set</dd>
				</div>
			{/if}
			{#if queryParams(event).length > 0}
				<div class="col-span-2 min-w-0">
					{@render fact('Query')}
					<dd>{@render paramChips(queryParams(event))}</dd>
				</div>
			{/if}
		</dl>
	{:else}
		<div class="flex flex-col gap-1.5 text-xs">
			<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
				{@render factRow('When')}
				<span class="font-medium" title={event.created_at}>{stamp.absolute}</span>
				{#if stamp.relative}
					<span class="text-muted-foreground">{stamp.relative}</span>
				{/if}
			</span>
			{#if event.actor_sub}
				<!-- The EMAIL is what identifies a person to anyone reading this log; the WorkOS subject is
			     plumbing and rode here as a raw `user_01K…` nobody can act on. It stays as the title,
			     for the one reader matching a support ticket. -->
				<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5" title={event.actor_sub}>
					{@render factRow('Actor')}
					<span class="font-medium">{led.actor}</span>
					{#if event.actor_email}
						<code class="text-muted-foreground font-mono">{event.actor_email}</code>
					{/if}
				</span>
			{/if}
			{#each details as [label, value] (label)}
				<!-- What this event recorded that no row above states — the parsed replacement for a block
			     of raw line-numbered JSON, which was the only place a custom key could be read and so
			     was load-bearing even though nobody wanted to read JSON. -->
				<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
					{@render factRow(label)}
					<span class="min-w-0 font-medium break-all">{value}</span>
				</span>
			{/each}
			<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
				{@render factRow('Type')}
				<code class="font-mono">{event.event_type}</code>
			</span>
			<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5" title={methodRoute(event)}>
				{@render factRow('Operation')}
				<span class="font-medium">{methodLabel(event.method)}</span>
				<code class="text-muted-foreground font-mono">{event.route}</code>
			</span>
			{#if queryParams(event).length > 0}
				<span class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
					{@render factRow('Query')}
					{@render paramChips(queryParams(event))}
				</span>
			{/if}
		</div>
	{/if}
</div>
