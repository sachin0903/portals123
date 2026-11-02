<script lang="ts" generics="T extends ThreadMessage">
	// The THREAD — the scrolling conversation itself, shared by every surface that shows one: the
	// platform inbox's direct messages, the inbox's group feed, the client record's Messages
	// section — and the CLIENT's own /messages page (`viewer="client"`). One implementation, so the
	// same conversation reads the same way whichever side you are on, and a change to how a turn is
	// drawn lands everywhere at once.
	//
	// Deliberately NOT in here: the header (who the thread is with — the record already says, the
	// inbox has to), the composer (what it posts to differs per surface) and the empty state (its
	// words do). This is the message list and nothing else.
	import { ClientAvatar, PersonAvatar, cn } from '@graphene/ui';
	import type { Snippet } from 'svelte';
	import CheckIcon from '@lucide/svelte/icons/check';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import { formatBytes } from '../../../bytes.js';
	import {
		attachmentIcon,
		formatMessageTime,
		groupMessagesByDay,
		groupMessagesIntoRuns,
		staffSenderLabel
	} from './messages.js';
	// The `generics=` attribute above is the only use of this type, and svelte-eslint-parser does
	// not look inside that attribute — so the rule has to be told, or it reads as a dead import.
	// eslint-disable-next-line @typescript-eslint/no-unused-vars
	import type { ThreadMessage } from './messages.js';

	let {
		messages,
		clientName,
		firstUnreadId = null,
		download,
		error = '',
		subline,
		scrollEl = $bindable(),
		search = '',
		viewer = 'staff',
		onFulfil,
		seenUpTo = null,
		onscroll
	}: {
		messages: T[];
		/**
		 * Whose eyes. `staff` is the book: every run carries a face and a name, because a thread
		 * can hold three colleagues and the client, and your own words are the same muted bubble
		 * as everyone else's with only the side telling them apart. `client` is the one-to-one
		 * chat a client has with their firm, drawn the way every messaging app they already use
		 * draws it: their own messages are the filled brand bubble on the right with no face and
		 * no "You" over them (there is only one of them), the firm's are the muted bubble on the
		 * left under the adviser's face and name. No role is printed — not "Client", and not the
		 * staff member's either; the page's "Your adviser" line already says who they are.
		 */
		viewer?: 'staff' | 'client';
		/** Lets a pending upload request be answered from its card (the client's side only; staff
		 *  cannot fulfil what they asked for). Absent ⇒ the card is read-only. */
		onFulfil?: (item: NonNullable<T['request_item']>) => void;
		/** The other side's read watermark (ISO). For a STAFF reader, the last of their own
		 *  messages at or before it is stamped "Seen". A client is never shown a receipt: whether
		 *  the firm has opened their message is not theirs to see. */
		seenUpTo?: string | null;
		/** The display name for a message the CLIENT sent. A client has no `auth.users` row, so
		 *  `sender_name` is null on their messages and the surface supplies the name: the fixed
		 *  client of a 1:1 thread, or the single recipient a group-tagged fulfilment names. */
		clientName: (entry: T) => string;
		/** The first message the reader had not seen when they arrived — where the "New" bar goes.
		 *  Null means no bar: nothing unread, or a feed with no read watermark of its own (a
		 *  group). Compute it ONCE, before marking the thread read, or it erases itself. */
		firstUnreadId?: number | null;
		download: (conversationUid: string, attachmentUid: string) => string;
		/** Shown at the head of the thread — a load failure, not a send failure (that belongs
		 *  beside the box you typed into). */
		error?: string;
		/** What the reader is looking for in THIS conversation. The BOX is not here — it lives in
		 *  the pane's header row, which is `shrink-0`, so it cannot scroll away with the messages it
		 *  filters. This is the term it produces. */
		search?: string;
		/** An extra line inside every bubble, above the body — the group feed's "To …". */
		subline?: Snippet<[T]>;
		/** The scroll container, so the surface can ask whether the reader is at the bottom before
		 *  it decides a live push has been seen. */
		scrollEl?: HTMLDivElement;
		onscroll?: () => void;
	} = $props();

	/** CLIENT-SIDE, over the messages already on screen, and that is honest here: a conversation is
	 *  loaded whole (`GET /messaging/conversations/{uid}/messages` returns the thread, not a page of
	 *  it), so there is no "not on this page" this can lie about — the trap `book/listing/query.ts`
	 *  exists to avoid. Matches the body and the sender's name, because "what did Craig say about
	 *  the transfer" is one question with two halves — and WHAT THE ROW SHOWS when it has no body:
	 *  a request card's title and an attachment's filename. A message the reader can see the word
	 *  on and cannot search for reads as a search that does not work. */
	const term = $derived(search.trim().toLowerCase());
	const haystack = (m: T) =>
		[
			m.body ?? '',
			m.sender_name ?? '',
			clientName(m),
			m.request_item?.title ?? '',
			...(m.attachments ?? []).map((a) => a.filename)
		]
			.join(' ')
			.toLowerCase();
	const visible = $derived(term ? messages.filter((m) => haystack(m).includes(term)) : messages);
	const dayGroups = $derived(groupMessagesByDay(visible));

	/** The viewer's own last message that the other side has read, if any — "Seen" goes under it.
	 *  Never for a client: a read receipt tells them when the firm opened their message. */
	const seenMessageId = $derived.by(() => {
		if (viewer === 'client' || !seenUpTo) return null;
		for (let i = visible.length - 1; i >= 0; i--) {
			const m = visible[i]!;
			if (m.sent_by_me === true) return m.created_at <= seenUpTo ? m.id : null;
		}
		return null;
	});
</script>

<!-- THE CONVERSATION TAKES THE PANE IT IS GIVEN. A capped column centred in a wide pane left a
     channel of empty card down each side and floated the firm's own replies away from the edge
     they are anchored to. Line length is not this container's problem: each bubble carries its
     own `max-w-[42rem]`, so a wide monitor gets more conversation, not longer lines.

     The horizontal padding is on the RUNS, not on this scroll container, so the day rules and the
     "New" bar reach both edges. Inset by a container's own padding they read as a box drawn
     inside a box, and a divider that stops short of the edge is not dividing anything. -->
<div class="flex flex-1 flex-col overflow-y-auto py-5" bind:this={scrollEl} {onscroll}>
	{#if error}
		<p class="text-destructive px-4 pb-3 text-center text-sm">{error}</p>
	{/if}
	{#if term && visible.length === 0}
		<!-- The term is the reader's own, so it is quoted back: "no messages" on a thread they can
		     see has messages in it reads as a fault rather than as a miss. -->
		<p class="text-muted-foreground px-4 py-6 text-center text-sm">
			No messages match “{search.trim()}”.
		</p>
	{/if}
	<!-- A CONVERSATION GROWS UPWARDS FROM THE BOX YOU TYPE INTO. `mt-auto` pushes a short thread
	     down to sit on the composer instead of hanging from the top of the pane with a metre of
	     empty card under it. `mt-auto` rather than `justify-end` on the scroller: with content
	     taller than the pane, `justify-end` puts the first message above the scroll origin where
	     it cannot be reached. -->
	<div class="mt-auto w-full space-y-5">
		{#each dayGroups as group, gi (gi)}
			<div>
				<!-- The day divider is a RULE with its label sitting on it. Centred text alone reads as
				     a stray line of prose between two messages; a rule says "everything below here is
				     a different day", which is the whole job. The pill paints the surface's own
				     background — `--background`, what both threads sit on — to break the line behind its
				     label, or the hairline runs through the word. -->
				<div class="mb-4 flex items-center justify-center">
					<span
						class="bg-muted/60 text-muted-foreground rounded-full px-3 py-1 text-xs font-medium"
					>
						{group.label}
					</span>
				</div>
				<!-- RUNS, not messages. Consecutive messages from one person within a few minutes share
				     an avatar and one header, because repeating "Craig Currie · Administrator · 22:54"
				     above every one-word reply makes the reader re-establish who is speaking five times
				     to read five words. The gap between runs is wider than the gap inside one, which is
				     what makes the grouping legible rather than merely denser. -->
				<div class="space-y-4">
					{#each groupMessagesIntoRuns(group.items) as run (run[0].id)}
						{#if firstUnreadId !== null && run.some((m) => m.id === firstUnreadId)}
							<!-- Where the reader left off. Above the RUN holding the first unread message
							     rather than above that message itself: a run is one person inside a few
							     minutes, so splitting one would put the bar between two halves of a single
							     turn. Full-bleed, and RED — `--destructive`, so it re-brands with the
							     tenant's own palette and is the one thing on the page wearing that colour,
							     which is what makes it findable while scrolling. -->
							<div class="flex items-center" role="separator" aria-label="New messages">
								<span class="bg-destructive/60 h-px flex-1"></span>
								<span class="text-destructive pr-4 pl-3 text-xs font-semibold">New</span>
							</div>
						{/if}
						{@const lead = run[0]}
						<!-- YOUR OWN messages on the right, everyone else's on the left — which includes the
						     firm's other advisers. `sent_by_me` is the server's answer (the browser holds a
						     WorkOS id and the row holds an `auth.users` uuid, so it cannot compare them
						     itself), and it is absent from a live push on purpose — see `live_push.push`
						     — so its absence reads as "not mine". -->
						{@const mine = lead.sent_by_me === true}
						<!-- WHOSE NAME is a different question from WHICH SIDE. A message from another
						     adviser is not mine — it belongs on the left — but it is still STAFF, so it
						     carries their name and their role. Only `sender_kind` can answer that. -->
						{@const fromStaff = lead.sender_kind === 'staff'}
						{@const senderName = fromStaff
							? staffSenderLabel(lead, viewer === 'client' ? 'Your firm' : 'Staff')
							: clientName(lead)}
						{@const senderRole =
							viewer === 'client'
								? null
								: fromStaff
									? lead.sender_is_support
										? null
										: lead.sender_role
									: 'Client'}
						<!-- A client's OWN run carries no face and no name: there is one of them, and a disc
						     with their initials beside every reply says nothing. Time only, once per run. -->
						{@const anonymous = viewer === 'client' && mine}
						<!-- Only the ROW is mirrored: the column inside stays `items-start` and prose stays
						     left-aligned, because right-aligned prose is the thing that is hard to read. -->
						<div class={cn('flex items-start gap-3 pr-4 pl-4', mine && 'flex-row-reverse')}>
							<!-- `fromStaff` already decided whose name this is, so it decides the disc too: a
							     colleague is one of several you tell apart down a thread, the client is the
							     one person you do not have to. -->
							{#if anonymous}
								<!-- No disc, but its width stays so the run's right edge lines up with the
								     firm's left one across the thread. -->
								<span class="hidden size-7 shrink-0 sm:block" aria-hidden="true"></span>
							{:else if fromStaff}
								<PersonAvatar name={senderName} size="sm" class="mt-0.5 shrink-0" />
							{:else}
								<ClientAvatar name={senderName} size="sm" class="mt-0.5 shrink-0" />
							{/if}
							<div
								class={cn('flex min-w-0 flex-1 flex-col gap-1', mine ? 'items-end' : 'items-start')}
							>
								<!-- Who and when, ONCE per run, above the bubbles rather than inside the first
								     one — inside, it made the first bubble of a run a different shape from every
								     other bubble in it. -->
								<div
									class={cn(
										'flex flex-wrap items-baseline gap-x-2 px-1',
										mine && 'flex-row-reverse'
									)}
								>
									{#if !anonymous}
										<span class="text-sm font-semibold">{senderName}</span>
										{#if senderRole}
											<span class="text-muted-foreground text-xs">{senderRole}</span>
										{/if}
									{/if}
									<span class="text-muted-foreground text-[11px]">
										{formatMessageTime(lead.created_at)}
									</span>
								</div>
								{#each run as entry, i (entry.id)}
									<!-- The tail is on the FIRST bubble of a run only — one corner un-rounded, on
									     the sender's own side. It points the run at whoever sent it, which is what
									     a speech bubble is for, and it costs no pseudo-element and nothing to
									     re-colour when a tenant re-brands: it is the same background simply not
									     curving away at that corner. On every bubble it reads as five separate
									     people talking. -->
									<div
										class={cn(
											'flex min-w-0 max-w-[42rem] flex-col items-start gap-1 rounded-xl px-3 py-2',
											mine
												? viewer === 'client'
													? 'bg-primary text-primary-foreground'
													: 'bg-primary/10'
												: 'bg-muted',
											i === 0 && (mine ? 'rounded-tr-none' : 'rounded-tl-none')
										)}
										title={formatMessageTime(entry.created_at)}
									>
										{#if subline}
											{@render subline(entry)}
										{/if}
										{@render body(entry)}
									</div>
									{#if entry.id === seenMessageId}
										<span class="text-muted-foreground px-1 text-[11px]">Seen</span>
									{/if}
								{/each}
							</div>
						</div>
					{/each}
				</div>
			</div>
		{/each}
	</div>
</div>

<!-- What is IN a bubble: a request card, or prose, plus every file on the message. Its own snippet
     so the run's first bubble and the rest cannot drift apart — the tail is the only thing that
     distinguishes them. -->
{#snippet requestCard(item: NonNullable<T['request_item']>, status: string)}
	{@const fulfilled = item.status === 'fulfilled'}
	<span
		class={cn(
			'flex size-8 shrink-0 items-center justify-center rounded-lg',
			fulfilled ? 'bg-primary text-primary-foreground' : 'bg-primary/10 text-primary'
		)}
	>
		{#if fulfilled}
			<CheckIcon class="size-4" />
		{:else}
			<UploadIcon class="size-4" />
		{/if}
	</span>
	<span class="min-w-0 flex-1">
		<span class="block truncate font-medium">Requested: {item.title}</span>
		<span class="text-muted-foreground block text-xs">{status}</span>
	</span>
{/snippet}

{#snippet body(entry: T)}
	{#if entry.request_item && entry.attachments.length === 0}
		{@const item = entry.request_item}
		{@const fulfilled = item.status === 'fulfilled'}
		<!-- Read-only on a staff surface — staff cannot fulfil their own request. The client's side
		     passes `onFulfil`, and a PENDING upload request becomes the tap-to-upload card. -->
		{@const answerable =
			onFulfil !== undefined && item.status === 'pending' && item.action === 'upload'}
		{#if answerable}
			<button
				type="button"
				onclick={() => onFulfil?.(item)}
				class="bg-background/60 text-foreground hover:bg-background flex w-full items-center gap-3 rounded-xl border border-dashed px-3 py-2 text-left text-sm"
			>
				{@render requestCard(item, 'Tap to upload a document')}
			</button>
		{:else}
			<div
				class="bg-background/60 text-foreground flex w-full items-center gap-3 rounded-xl border px-3 py-2 text-left text-sm"
			>
				{@render requestCard(
					item,
					fulfilled
						? viewer === 'client'
							? 'Sent'
							: 'Received'
						: item.status === 'cancelled'
							? 'Cancelled'
							: viewer === 'client'
								? 'Waiting — manage this from your Requests page'
								: 'Pending'
				)}
			</div>
		{/if}
	{:else if entry.body}
		<p class="text-sm leading-relaxed whitespace-pre-wrap break-words">{entry.body}</p>
	{/if}
	<!-- EVERY file on the message (0123), each its own download row. `w-64` rather than `max-w-md`:
	     a row sized to its own filename makes every bubble a different width, so a run of three
	     attachments reads as three unrelated blocks — and the name is truncated either way, so the
	     extra width buys nothing. -->
	{#each entry.attachments as att (att.uid)}
		{@const Glyph = attachmentIcon(att)}
		<a
			href={download(entry.conversation_uid, att.uid)}
			download={att.filename}
			class="bg-background/60 hover:bg-background text-foreground flex w-64 max-w-full items-center gap-3 rounded-xl border px-3 py-2 text-sm"
		>
			<span
				class="bg-muted text-muted-foreground flex size-8 shrink-0 items-center justify-center rounded-lg"
			>
				<Glyph class="size-4" />
			</span>
			<span class="min-w-0 flex-1">
				<span class="block truncate font-medium" title={att.filename}>{att.filename}</span>
				<span class="text-muted-foreground block truncate text-xs">
					{formatBytes(att.size_bytes)}
				</span>
			</span>
			<DownloadIcon class="text-muted-foreground size-4 shrink-0" />
		</a>
	{/each}
{/snippet}
