<script lang="ts">
	// The client record's "Messages" section — a thread-only view of the SAME conversation the
	// platform inbox's Direct Messages shows for this client. Deliberately narrower than that
	// inbox: no conversation list, because the client is already fixed by the record. What the
	// inbox does NOT have is the right-hand rail below — who is on the conversation, every file
	// that has crossed it, and what this client still owes the firm — which only makes sense on a
	// surface where the client is fixed.
	import { ClientAvatar, clientTeamRoleLabel, orderClientTeam } from '@graphene/ui';
	import { tick } from 'svelte';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { readingContact } from '$lib/reading.svelte';
	import type { TaskRow } from '@graphene/ui/book/tasks';
	import { RequestDialog, type ApiRequest } from '@graphene/ui/book/documents';
	import type { ContactAdviser } from '$lib/api';
	import {
		MessageComposer,
		ConversationPanels,
		MessageRail,
		MessageThread,
		type ApiConversation,
		type ApiMessage,
		type RailAccount,
		type RailClient,
		type RailNote
	} from '@graphene/ui/book/messages';

	let {
		conversation,
		initialMessages,
		canRequestDocuments = false,
		canWriteMessages = true,
		advisers = [],
		client = null,
		accounts = [],
		notes = [],
		onAddNote,
		onDeleteNote,
		requests = [],
		tasks = [],
		links
	}: {
		conversation: ApiConversation;
		initialMessages: ApiMessage[];
		canRequestDocuments?: boolean;
		/** `messages:write`. Absent ⇒ the thread is read-only (no composer). Default true so a
		 *  mount that already gated the page on write (the inbox) does not have to restate it. */
		canWriteMessages?: boolean;
		/** WHO THE THREAD IS WITH. Stated here as well as in the inbox: the record's header names
		 *  them, but the facts an adviser reaches for mid-conversation — the address to reply to,
		 *  the number to ring, the reference to quote — were a tab away, and the same rail in the
		 *  inbox had them. No `clientHref` goes with it: this IS their record. */
		client?: RailClient | null;
		/** What they hold, the same summary the inbox rail states. */
		accounts?: RailAccount[];
		/** The recent notes on this client. */
		notes?: RailNote[];
		/** Write one from here; absent ⇒ no composer. */
		onAddNote?: (body: string) => Promise<string | null>;
		/** Withdraw one of your own from here; absent ⇒ no delete. */
		onDeleteNote?: (uid: string) => Promise<string | null>;
		// The rail's panels. Each defaults to empty, so a caller that wants the bare thread
		// (or one whose reader lacks the permission behind a panel) gets a rail without it rather
		// than a rail that is permanently loading.
		advisers?: ContactAdviser[];
		requests?: ApiRequest[];
		tasks?: TaskRow[];
		links?: {
			request: (uid: string) => string;
			task: (uid: string) => string;
			account?: (uid: string) => string;
		};
	} = $props();

	// Captured once, deliberately not read reactively anywhere below: `markRead()` calls
	// `invalidate()`, which (because this page's load calls `parent()`) reruns this page's OWN
	// load too — handing this component a brand-new `conversation` object with the same id and
	// contact_id. The realtime effect below used to read `conversation.id`/`conversation.contact_id`
	// straight off the prop, which made Svelte treat that new object as a changed dependency:
	// the effect tore down and reran, calling `markRead()` again, which invalidated again — an
	// infinite loop, invisible on screen, that silently re-marked this conversation read every
	// few seconds for as long as this tab stayed open regardless of what page was actually being
	// looked at (root cause of the unread dot never appearing without a refresh). Neither value
	// can legitimately change for the lifetime of this component (a new client means a new page,
	// which remounts it), so there is nothing to gain from tracking them.
	// svelte-ignore state_referenced_locally
	const conversationId = conversation.id;
	// svelte-ignore state_referenced_locally
	// What a `/chats/{uid}` path names (0091_messaging_uids); the id above still keys the
	// dependency the record's layout registers and the realtime payloads it compares.
	const conversationUid = conversation.uid;
	// svelte-ignore state_referenced_locally
	const contactId = conversation.contact_id;

	// Seeded once from props, then independently mutable (send/live-push append to it) — not
	// meant to track a later prop change.
	// svelte-ignore state_referenced_locally
	let messages = $state(initialMessages);

	/**
	 * The first message the reader had NOT seen when they arrived — what the "New" bar marks.
	 *
	 * Computed once, from the props, and never recomputed. It cannot be `$derived`: `markRead()`
	 * fires on mount, so a reactive read of the watermark would erase the marker the instant the
	 * thread opened, which is the one moment it exists to be useful. Holding it still also keeps a
	 * message that arrives while you are watching from being labelled new — it appears below the
	 * bar, where it belongs, rather than dragging the bar down to itself.
	 *
	 * Only the CLIENT's messages count. The firm's own are read by definition, and a watermark
	 * older than a reply you sent yourself would otherwise put "New" above your own words.
	 */
	const firstUnreadId = (() => {
		const seenUpTo = conversation.staff_last_read_at;
		const first = initialMessages.find(
			(m) => m.sender_kind !== 'staff' && (!seenUpTo || m.created_at > seenUpTo)
		);
		return first?.id ?? null;
	})();
	let draft = $state('');
	let sending = $state(false);
	let sendError = $state('');
	let requestDialogOpen = $state(false);

	let messagesEl: HTMLDivElement | undefined = $state();

	// The rail's visibility override, shared with its toggle: null until the reader clicks, and
	// then it holds at every width. Everything else about the rail — its panels, its sections and
	// which of them are shut — belongs to the component.

	function isNearBottom(el: HTMLDivElement, thresholdPx = 48): boolean {
		return el.scrollHeight - el.scrollTop - el.clientHeight <= thresholdPx;
	}

	async function scrollToBottom() {
		await tick();
		if (!messagesEl) return;
		messagesEl.scrollTop = messagesEl.scrollHeight;
	}

	// Tells the root layout's Messages nav dot (both the global one and this client's own) to
	// recheck NOW rather than on its own up-to-30s poll tick — see MessagesInbox.svelte's
	// identical helper.
	function notifyUnreadChanged() {
		if (typeof window !== 'undefined') window.dispatchEvent(new Event('chats:unread-changed'));
	}

	async function markRead() {
		await fetch(`/chats/${conversationUid}/read`, { method: 'POST' });
		// Tells the root layout's own poller for this client's Messages dot to recheck NOW rather
		// than on its own up-to-30s tick (see that poller in `routes/+layout.svelte`).
		notifyUnreadChanged();
	}

	// Files staged for the next send — held here because `send()` below is what posts them, and
	// the composer binds this list. All of them ride ONE message (0123): a note and the three
	// documents it is about are one thing.
	let draftFiles = $state<File[]>([]);
	let threadSearch = $state('');

	async function send(e: SubmitEvent) {
		e.preventDefault();
		const text = draft.trim();
		if ((!text && draftFiles.length === 0) || sending) return;
		sending = true;
		sendError = '';

		let res: Response;
		if (draftFiles.length > 0) {
			const body = new FormData();
			// A repeated `file` part, which is what the endpoint's `list[UploadFile]` reads. One
			// request, one message, every file on it.
			for (const file of draftFiles) body.append('file', file, file.name);
			body.append('text', text);
			res = await fetch(`/chats/${conversationUid}/attachments`, { method: 'POST', body });
		} else {
			res = await fetch(`/chats/${conversationUid}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ text })
			});
		}
		sending = false;

		if (!res.ok) {
			sendError =
				((await res.json().catch(() => null))?.error as string | undefined) ??
				'Could not send that.';
			return;
		}
		const message: ApiMessage = await res.json();
		messages = [...messages, message];
		draft = '';
		draftFiles = [];
		await scrollToBottom();
	}

	// Tell the shell's notification bell which thread is on screen, so a message arriving in it is
	// marked read rather than popped as a toast about something you just watched land. Returning
	// the cleanup from the effect is what guarantees it is cleared on navigation — a claim left
	// standing would swallow that client's next notification from every other page.
	$effect(() => readingContact(contactId));

	// Live push while this section is open — same single-process prototype the platform inbox
	// uses (apps/api/app/live_push.py). Filtered to just this conversation: a push for a
	// different client belongs in the inbox, not this record.
	$effect(() => {
		const events = new EventSource('/messages/events');

		async function handleMessage(event: MessageEvent) {
			const payload = JSON.parse(event.data);
			if (payload.type === 'resync') {
				await refreshMessages();
				return;
			}
			if (payload.type !== 'message' || payload.conversation_id !== conversationId) return;
			// The sender's own send already appended this message optimistically before its
			// broadcast echoes back on this same staff_key connection (see MessagesInbox's fix
			// for the identical issue on the group feed).
			if (messages.some((m) => m.id === payload.message.id)) return;
			// Requires the tab to actually be VISIBLE AND FOCUSED, not just scrolled to the
			// bottom — this component stays mounted (stream connected) in a backgrounded tab, and a
			// thread left scrolled to the bottom (its default) was auto-marking every live push
			// read before a human ever saw it, which is exactly why the nav dot never got a chance
			// to light up. `document.hidden` alone isn't enough: it only goes true when the TAB
			// itself is switched away from or the window minimised — a chat left open in its own
			// (background, unfocused) window while the human works in a different app or a
			// different window reports `hidden: false` the whole time, so `document.hasFocus()` is
			// the second half of "actually being looked at".
			// An ABSENT scroll container counts as at-the-bottom, not as away from it. The empty
			// state renders a different element with no `bind:this`, so `messagesEl` is undefined
			// on a conversation with no messages yet — and the FIRST message into a fresh thread
			// therefore lit the unread dot while the reader was staring straight at it. There is
			// nothing to have scrolled away from when there is nothing to scroll.
			const seenImmediately =
				(!messagesEl || isNearBottom(messagesEl)) &&
				typeof document !== 'undefined' &&
				!document.hidden &&
				document.hasFocus();
			messages = [...messages, payload.message];
			if (seenImmediately) {
				await scrollToBottom();
				await markRead();
			} else {
				notifyUnreadChanged();
			}
		}

		async function refreshMessages() {
			const response = await fetch(`/chats/${conversationUid}`);
			if (response.ok) messages = await response.json();
			const seenImmediately =
				(!messagesEl || isNearBottom(messagesEl)) &&
				typeof document !== 'undefined' &&
				!document.hidden &&
				document.hasFocus();
			if (seenImmediately) {
				await scrollToBottom();
				await markRead();
			} else {
				notifyUnreadChanged();
			}
		}

		events.addEventListener('open', () => void refreshMessages());
		events.addEventListener('message', (event) => void handleMessage(event));
		scrollToBottom();
		markRead();

		return () => {
			events.close();
		};
	});
</script>

<!-- ONE CARD holding the thread and the rail, split by a hairline — not two boxes with a gap
     between them. The SAME card the platform inbox puts its conversation in: the two surfaces
     mount the same components on the same conversation, and one of them floating on the page
     background while the other sat on a card made them read as two different screens.

     A ROW from `lg` up, a column below it, so the rail stacks under the thread on a narrow screen
     rather than squeezing it. `min-w-0` on the thread is what stops a long filename in a bubble
     from pushing the rail off the page. -->
<div
	class="bg-card flex h-full min-h-[420px] flex-col overflow-hidden rounded-xl shadow-xs lg:flex-row"
>
	<ConversationPanels
		bind:search={threadSearch}
		{messages}
		download={(uid, attachment) => `/chats/${uid}/attachments/${attachment}`}
	>
		{#if messages.length === 0}
			<!-- `justify-end`: the prompt sits directly above the composer rather than floating in the
			     middle of an empty card. A conversation grows upwards from the box you type into, so the
			     invitation to start one belongs where the first message will appear — not stranded in
			     the middle with blank space beneath it. -->
			<div class="flex flex-1 flex-col items-center justify-end gap-4 px-6 pt-10 pb-4 text-center">
				<ClientAvatar name={conversation.contact_name ?? 'Client'} size="lg" />
				<div>
					<p class="font-semibold">{conversation.contact_name ?? 'This client'}</p>
					<p class="text-muted-foreground mt-1 text-sm">
						Send a message to start the conversation.
					</p>
				</div>
			</div>
		{:else}
			<!-- The thread, drawn by the shared renderer the platform inbox mounts too: one
			     implementation of a day rule, a run, a bubble and the "New" bar, so this section and
			     the inbox cannot read differently for the same conversation. -->
			<MessageThread
				{messages}
				clientName={() => conversation.contact_name ?? 'Unknown'}
				{firstUnreadId}
				search={threadSearch}
				download={(uid, attachment) => `/chats/${uid}/attachments/${attachment}`}
				bind:scrollEl={messagesEl}
			/>
		{/if}

		<!-- ONE composer, docked at the foot of the card in BOTH states. The empty state used to carry
		     its own copy inside a `justify-center` column, so on a fresh conversation the reply box
		     floated in the middle of the card with blank space under it, and moved down to the bottom
		     the moment a first message arrived. Full width of the card, too: it was `w-1/2`, half the
		     width of the messages it answered. Read-only visitors (`messages:read` without write)
		     see the thread without a box they cannot send from. -->
		{#if canWriteMessages}
			<div class="w-full px-4 pb-4">
				{@render composer()}
			</div>
		{/if}
	</ConversationPanels>
	<MessageRail
		people={[
			{ name: conversation.contact_name ?? 'This client', role: 'Client', isClient: true },
			...orderClientTeam(advisers).map((a) => ({ name: a.name, role: clientTeamRoleLabel(a.role) }))
		]}
		peopleNote="No adviser is assigned to this client."
		{client}
		{accounts}
		{notes}
		{onAddNote}
		{onDeleteNote}
		{requests}
		{tasks}
		showTasks={false}
		{links}
	/>
</div>

{#snippet composer()}
	<MessageComposer
		bind:draft
		bind:files={draftFiles}
		error={sendError}
		{sending}
		submit={send}
		onRequest={canRequestDocuments ? () => (requestDialogOpen = true) : undefined}
	/>
{/snippet}

<!-- "Request a document" from the composer — the same builder the client's Documents tab uses,
     locked to this record's client (this page's own `createRequest` action). -->
<RequestDialog
	{enhance}
	invalidate={invalidateAll}
	bind:open={requestDialogOpen}
	action="?/createRequest"
	forClient={{
		contact_id: conversation.contact_id,
		name: conversation.contact_name,
		email: null
	}}
/>
