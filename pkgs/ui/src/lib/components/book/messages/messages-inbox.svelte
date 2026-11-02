<script lang="ts">
	import {
		Button,
		ClientAvatar,
		Dialog,
		Input,
		Label,
		MultiSelect,
		Skeleton,
		cn
	} from '@graphene/ui';
	import { tick } from 'svelte';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SearchIcon from '@lucide/svelte/icons/search';
	// The thread itself is the shared renderer — the same one the client record's Messages section
	// mounts, so a conversation reads identically from either surface (day rules, runs, bubbles,
	// the "New" bar). Everything left in this file is the inbox's own: the list, the header, the
	// composers and what they post to.
	// The app-wide ticking clock. Read inside the template, every row's timestamp re-computes
	// on each tick — without it "Now" is frozen at whatever it said when the row first
	// rendered, which is the whole complaint: a list that is only right when you reload it.
	import { now } from '../../notifications/now.svelte.js';
	import MessageComposer from './message-composer.svelte';
	import ConversationPanels from './conversation-panels.svelte';
	import MessageRail from './message-rail.svelte';
	import MessageThread from './message-thread.svelte';
	import type { ApiRequest } from '../documents/index.js';
	import type { TaskRow } from '../tasks/index.js';
	import {
		formatListTime,
		type ApiConversation,
		type ConversationTab,
		type RailAccount,
		type RailClient,
		type RailNote,
		type RailPerson,
		type ApiGroup,
		type ApiGroupFeedMessage,
		type ApiMessage
	} from './messages.js';

	export type MessagesRoutes = {
		conversations: string;
		search: (query: string) => string;
		/** Every client the caller may message, narrowed by the same term the conversation search
		 *  uses. A function rather than a bare URL because the narrowing is SERVER-side: this list
		 *  is the firm's whole book, and a fixed page of it filtered in the browser puts every
		 *  client past the cut silently out of reach. */
		contacts: (query: string) => string;
		groups: string;
		conversation: (uid: string) => string;
		group: (uid: string) => string;
		conversationAttachments: (uid: string) => string;
		groupAttachments: (uid: string) => string;
		read: (uid: string) => string;
		download: (conversationUid: string, attachmentUid: string) => string;
		events: string;
		/** The rail's three fetched panels for one client, in ONE round trip — advisers, open
		 *  requests and open tasks. Optional: a mount that does not serve it (the admin book's
		 *  read-only one) gets a rail naming the client and its files rather than one that is
		 *  permanently loading. Takes BOTH of the client's handles: the uid because
		 *  `GET /crm/contacts/{contact_uid}` is uid-addressed, and the serial because the two
		 *  listings are keyed by it — sending both is what lets all three reads start at once. */
		rail?: (contactUid: string, contactId: number) => string;
		/** Where a note written beside the thread is POSTed. Absent ⇒ the rail offers no composer.
		 *  The MOUNT withholds it, not `canWrite`: a note is `contacts:write`, which is a different
		 *  permission from the `messages:write` that governs sending — and only the server knows
		 *  which of the two this reader holds. */
		notes?: (contactUid: string) => string;
		/** Where one note is DELETEd from. Absent ⇒ the rail offers no delete, the same gate as
		 *  `notes` (both are `contacts:write`); the API keeps it to the caller's own notes. */
		note?: (contactUid: string, noteUid: string) => string;
	};

	export type MessageRequestTarget =
		| { kind: 'conversation'; contact_id: number; name: string | null }
		| { kind: 'group'; id: number; uid: string; name: string; refresh: () => Promise<void> };

	type DirectoryContact = {
		id: number;
		name: string | null;
		kind?: 'individual' | 'organisation';
	};

	/** One line in the list: a client, and the conversation with them when one has been opened.
	 *  A client nobody has messaged yet is a row here exactly like one who has — opening the thread
	 *  is what clicking them does, so there is nothing left to "start" separately. */
	type DirectoryRow = {
		contactId: number;
		name: string | null;
		/** Which disc the row draws. A conversation row does not carry it — `ApiConversation` has no
		 *  type — so a client with a thread shows the person disc until the book lands and the merge
		 *  below supplies the real one. */
		kind?: 'individual' | 'organisation';
		conversation: ApiConversation | null;
	};

	let {
		conversations: initialConversations,
		groups: initialGroups = [],
		/** Open this thread on arrival — the header search's `?conversation=` / `?group=`. */
		openConversation: openConversationUid = null,
		openGroup: openGroupUid = null,
		canRequestDocuments = false,
		canWrite = true,
		showTasks = true,
		showDirectory = true,
		showGroups = false,
		routes,
		links,
		onRequest,
		onReadingContact
	}: {
		conversations: ApiConversation[];
		groups?: ApiGroup[];
		openConversation?: string | null;
		openGroup?: string | null;
		canRequestDocuments?: boolean;
		canWrite?: boolean;
		showTasks?: boolean;
		showDirectory?: boolean;
		/** Offer the Groups shortcut in the sidebar. Off by default — see the section's own note. */
		showGroups?: boolean;
		routes: MessagesRoutes;
		/** Where the rail's owed rows point. Absent ⇒ they render unlinked, so a portal without
		 *  those pages does not send its reader to a URL it does not have. */
		links?: {
			request: (uid: string) => string;
			task: (uid: string) => string;
			/** The record of the person a thread is with — the thread header's name.
			 *
			 *  Deliberately NOT applied to the conversation list rows: each of those is a `<button>`
			 *  that selects the thread, and an anchor inside it would nest two interactive elements
			 *  competing for the same click. The header is where a reader goes when they want the
			 *  person rather than the conversation. Optional, so a mount with no client route states
			 *  the name plainly instead of offering a dead link. */
			client?: (uid: string) => string;
			/** An account on the rail's summary. Optional for the same reason. */
			account?: (uid: string) => string;
		};
		onRequest?: (target: MessageRequestTarget) => void;
		onReadingContact?: (contactId: number) => void | (() => void);
	} = $props();

	// Seeded once from props, then independently mutable (search results/live pushes replace it) —
	// not meant to track a later prop change, so the one-time read is intentional.
	// svelte-ignore state_referenced_locally
	let conversations = $state(initialConversations);
	// svelte-ignore state_referenced_locally
	let groupsList = $state(initialGroups);
	let query = $state('');

	// "Unread only" — CLIENT-SIDE over whatever `conversations` currently holds (the initial page
	// or a server search result): `unread` already rides every row, so no round-trip is needed.
	//
	// Deliberately leaves `groupsList` untouched. `ApiGroup` carries no `unread` field at all —
	// every group-tagged message is staff-sent, so there is nothing analogous to a client's unread
	// reply to flag (see messages.ts). Filtering groups by a value that does not exist on them has
	// exactly two honest outcomes: show every group (the filter simply does not apply here) or hide
	// every group (the filter can never be satisfied). The second reads as a bug — a pane that goes
	// blank the moment you tick a box that says nothing about groups — so this takes the first.
	let unreadOnly = $state(false);

	// A single selection spans BOTH sidebar sections — a group is a staff-only merged view over
	// several real conversations, never a conversation itself, so which kind is picked matters
	// throughout (what gets fetched, what the composer posts to, what the header shows).
	// Both handles: `id` is what a row and a realtime payload are compared by, `uid` is what a path
	// names (0091_messaging_uids) — see docs/api.md → "Addressing".
	// `contactUid` rides the SELECTION rather than being looked up from `conversations` when the
	// thread opens. Looking it up there read `conversations` inside the effect below — which the
	// open then WRITES (the read receipt patches the row) — so the effect retriggered itself and
	// re-fetched the thread and its rail for as long as the page stayed open. Nothing on screen
	// said so except the rail's panels clearing and repopulating over and over.
	type Selection =
		| { kind: 'conversation'; id: number; uid: string; contactUid: string; contactId: number }
		| { kind: 'group'; id: number; uid: string };

	function conversationSelection(row: ApiConversation): Selection {
		return {
			kind: 'conversation',
			id: row.id,
			uid: row.uid,
			contactUid: row.contact_uid,
			contactId: row.contact_id
		};
	}

	let selection = $state<Selection | null>(
		(() => {
			// The asked-for thread, when the list already holds it. Otherwise the first row, so a
			// link to a conversation that has since gone still opens the inbox rather than nothing.
			const asked = openConversationUid
				? initialConversations.find((c) => c.uid === openConversationUid)
				: null;
			if (asked) return conversationSelection(asked);
			const askedGroup = openGroupUid ? initialGroups.find((g) => g.uid === openGroupUid) : null;
			if (askedGroup) return { kind: 'group', id: askedGroup.id, uid: askedGroup.uid };
			if (initialConversations[0]) return conversationSelection(initialConversations[0]);
			// Only when the sidebar actually offers groups. Otherwise a firm with no conversations
			// yet would open on a group it cannot see selected, with no way to get back to it.
			return showGroups && initialGroups[0]
				? { kind: 'group', id: initialGroups[0].id, uid: initialGroups[0].uid }
				: null;
		})()
	);

	let messages = $state<ApiMessage[]>([]);
	// The first message the reader had NOT seen when they opened this conversation — what the
	// thread's "New" bar marks. Set once per open, in `openConversation`, BEFORE the read receipt
	// goes: a derived watermark would erase the marker the instant the thread opened, which is the
	// one moment it is useful. Holding it still also keeps a message that arrives while you are
	// watching from being labelled new — it appears below the bar, where it belongs.
	let firstUnreadId = $state<number | null>(null);
	let messagesError = $state('');
	/** THE READ IS IN FLIGHT — a different state from "this conversation has nothing in it", and
	 *  the two used to render the same. Every thread the reader clicked flashed "Send a message to
	 *  start the conversation" before its messages landed, which is a false statement about a
	 *  client you have been talking to for a year. Set before the fetch, cleared whichever way it
	 *  ends.
	 *
	 *  SEEDED TRUE WHEN SOMETHING IS ALREADY SELECTED, which on this page is always: the first
	 *  conversation is chosen at construction and its read is fired by a mount effect, so a flag
	 *  starting `false` put that same false statement on screen for the first frame of every load
	 *  and every reload — before any click had happened. */
	// svelte-ignore state_referenced_locally
	let loadingThread = $state(selection !== null);
	let groupFeed = $state<ApiGroupFeedMessage[]>([]);
	let groupMembers = $state<{ contact_id: number; contact_name: string | null }[]>([]);
	let groupFeedError = $state('');
	let groupSendError = $state('');
	let draft = $state('');
	// Several files ride ONE message (0123), so this is a list — the picker takes a set and the
	// endpoint reads a repeated `file` part.
	let draftFiles = $state<File[]>([]);
	let sendError = $state('');
	let sending = $state(false);

	// The rail's visibility override, shared with its toggle: null leaves it to the breakpoint.
	// The rail's FETCHED panels for the selected client. Cleared the moment the selection changes
	// and before the fetch lands, because showing the last client's open requests against this one
	// is a false statement about who owes what.
	let rail = $state<{
		client: RailClient | null;
		accounts: RailAccount[];
		notes: RailNote[];
		advisers: RailPerson[];
		requests: ApiRequest[];
		tasks: TaskRow[];
	}>({
		client: null,
		accounts: [],
		notes: [],
		advisers: [],
		requests: [],
		tasks: []
	});

	// Read state follows what's actually on screen, not merely which conversation is selected —
	// scrolled up reading history when a message lands, it must stay unread, same as Slack/Teams.
	/** Cleared on every selection change below: a term typed on one client would otherwise narrow
	 *  the next one's thread to nothing, with the box that did it two panes away. */
	let threadSearch = $state('');
	let conversationTab = $state<ConversationTab>('chat');
	let messagesEl: HTMLDivElement | undefined = $state();
	let pinnedToBottom = true;

	function isNearBottom(el: HTMLDivElement, thresholdPx = 48): boolean {
		return el.scrollHeight - el.scrollTop - el.clientHeight <= thresholdPx;
	}

	// Tells the root layout's Messages nav dot to recheck NOW rather than on its own up-to-30s poll
	// tick — after marking something read (clears instantly) and after a live push for a
	// conversation that isn't open (shows up instantly while this component is mounted).
	function notifyUnreadChanged() {
		if (typeof window !== 'undefined') window.dispatchEvent(new Event('chats:unread-changed'));
	}

	// A thread opens at its NEWEST message, the way every chat does. Getting here and giving up
	// leaves the reader at the TOP of the history they just opened, scrolling down to find what
	// they came for.
	//
	// One `tick()` is not enough on its own. The scroll container belongs to the THREAD and
	// reaches this component through `bind:scrollEl`, so the tick that renders it does not
	// necessarily land the binding back in this component's state in the same flush; and while a
	// conversation is still loading the inbox renders a different element with no binding at all
	// (see the note at the live-push handler). Both leave `messagesEl` undefined at this point,
	// and the old early return meant nothing ever pinned it afterwards.
	//
	// So pin now if we can, and again on the next frame: by then the container exists and its
	// content -- late avatars and attachments included -- has laid out, which is the other way a
	// scroll set too early lands short.
	async function scrollToBottom() {
		await tick();
		pinnedToBottom = true;
		const pin = () => {
			if (!messagesEl) return;
			messagesEl.scrollTop = messagesEl.scrollHeight;
		};
		pin();
		if (typeof requestAnimationFrame === 'function') requestAnimationFrame(pin);
	}

	// Marking a conversation read patches BOTH fields: `unread` is the dot in the list, and
	// `staff_last_read_at` is what `firstUnreadId` is computed from the next time this same session
	// opens the thread. Leave the stamp behind and the "New" bar comes back over messages this very
	// visit has already read.
	function noteRead(id: number) {
		const readAt = new Date().toISOString();
		conversations = conversations.map((c) =>
			c.id === id ? { ...c, unread: false, staff_last_read_at: readAt } : c
		);
	}

	async function markSelectedRead() {
		if (!canWrite || selection?.kind !== 'conversation') return;
		const { id, uid } = selection;
		await fetch(routes.read(uid), { method: 'POST' });
		noteRead(id);
		notifyUnreadChanged();
	}

	function onMessagesScroll() {
		if (!messagesEl) return;
		pinnedToBottom = isNearBottom(messagesEl);
		if (!pinnedToBottom) return;
		if (selection?.kind === 'conversation' && selectedConversation?.unread) markSelectedRead();
	}

	// THE WHOLE BOOK IS THE LIST. `conversations` is only the clients somebody has already messaged
	// (`list_conversations_for_tenant` — a client with no thread has no row yet), so on its own it
	// answers "who have we talked to", never "who can I talk to". These are every client the caller
	// may see, merged into the same list below; picking one that has no thread opens it, which is
	// what the separate "New direct message" dialog used to do. Two surfaces partitioning the book
	// between them — the list showing clients WITH a channel and a dialog showing the ones WITHOUT
	// — meant no surface ever showed the set you were actually choosing from.
	let contacts = $state<DirectoryContact[]>([]);
	let directoryError = $state('');
	// The client whose thread is being opened right now, so a second click cannot post a second
	// create for the same person while the first is in flight.
	let opening = $state<number | null>(null);

	// "New group" — a staff-only saved shortcut to a set of related clients (households/joint
	// accounts). Fans messages out into each member's own real conversation; a client never
	// perceives that grouping exists.
	let groupDialogOpen = $state(false);
	let groupName = $state('');
	let groupMemberIds = $state<string[]>([]);
	let groupError = $state('');
	let creatingGroup = $state(false);
	// Its own copy of the book, deliberately NOT the directory's: that one tracks the search box,
	// and a group picker offering only the clients matching whatever is typed in a box behind the
	// dialog would be a different set every time it opened.
	let groupPickerContacts = $state<DirectoryContact[]>([]);

	const selectedConversation = $derived.by(() => {
		if (selection?.kind !== 'conversation') return null;
		const id = selection.id;
		return conversations.find((c) => c.id === id) ?? null;
	});
	// Tell the shell's notification bell which thread is on screen, so a message arriving in the
	// open conversation is marked read rather than popped as a toast about something you just
	// watched land. Re-runs on every selection change and returns its own cleanup, so switching
	// conversations hands the claim over rather than leaving the old one standing.
	//
	// 1:1 only. A group message raises no adviser-side notification (the trigger's event list is
	// the direct-message pair), so there is nothing to suppress and claiming one would be a
	// statement about a notification that never arrives.
	$effect(() => {
		const contactId = selectedConversation?.contact_id;
		if (contactId === undefined) return;
		return onReadingContact?.(contactId);
	});

	const selectedGroup = $derived.by(() => {
		if (selection?.kind !== 'group') return null;
		const id = selection.id;
		return groupsList.find((g) => g.id === id) ?? null;
	});
	// A conversation names the client and their advisers; a group names its members. Both state the
	// relationship on THIS conversation, never a job title — the record says one thing about a
	// person and a second vocabulary here would disagree with it.
	const railPeople = $derived<RailPerson[]>(
		selectedConversation
			? [
					{
						name: selectedConversation.contact_name ?? 'This client',
						role: 'Client',
						isClient: true
					},
					...rail.advisers
				]
			: groupMembers.map((m) => ({ name: m.contact_name, role: 'Client', isClient: true }))
	);

	// Live threads first, in the order the server returned them (most recently spoken to), then
	// everyone else by name. Recency is what makes an inbox an inbox, and folding the untouched
	// half of the book into that ordering by an accident of the alphabet would push the
	// conversation somebody is in the middle of below a client they have never written to.
	const directory = $derived.by(() => {
		const byContact = new Map(contacts.map((c) => [c.id, c]));
		const rows: DirectoryRow[] = conversations.map((c) => ({
			contactId: c.contact_id,
			name: c.contact_name,
			kind: byContact.get(c.contact_id)?.kind,
			conversation: c
		}));
		const withChannel = new Set(conversations.map((c) => c.contact_id));
		const rest = contacts
			.filter((c) => !withChannel.has(c.id))
			.sort((a, b) => (a.name ?? '').localeCompare(b.name ?? ''));
		for (const c of rest)
			rows.push({ contactId: c.id, name: c.name, kind: c.kind, conversation: null });
		return rows;
	});
	const visibleRows = $derived(
		unreadOnly ? directory.filter((r) => r.conversation?.unread) : directory
	);

	const contactOptions = $derived(
		groupPickerContacts.map((c) => ({ value: String(c.id), label: c.name ?? 'Unknown' }))
	);

	// Failure leaves the clients WITH a thread listed rather than emptying the pane — the half that
	// came from the page load is still true, and an inbox that goes blank because a second read
	// failed is worse than one missing the names nobody has written to.
	async function loadContacts(q: string) {
		const res = await fetch(routes.contacts(q.trim()));
		if (!res.ok) {
			directoryError = "Couldn't load the rest of your clients.";
			return;
		}
		directoryError = '';
		contacts = await res.json();
	}

	async function ensureGroupPickerContacts() {
		if (groupPickerContacts.length > 0) return;
		const res = await fetch(routes.contacts(''));
		if (res.ok) groupPickerContacts = await res.json();
	}

	async function search(q: string) {
		const res = await fetch(routes.search(q.trim()));
		if (res.ok) conversations = await res.json();
	}

	// `q` defaults to the box's live value, not a snapshot taken at the call site: the push handler
	// below calls this bare (`refreshGroups()`) and still wants whatever is currently typed.
	async function refreshGroups(q: string = query) {
		// `routes.groups` is a bare base URL — also the group-CREATE POST target (`createGroup`
		// below), unlike `routes.search`, which is a query-building function. So the `?q=` is
		// appended here rather than injected by the app, the same way apps build `routes.search`'s
		// URL. `GET /messaging/groups` takes `q` and searches the group NAME server-side
		// (apps/api/app/routers/messaging.py `list_groups`, messaging_groups.list_groups_for_tenant)
		// — verified against the router rather than assumed, since the param name is exactly what a
		// silent mismatch here would get wrong.
		const trimmed = q.trim();
		const url = trimmed ? `${routes.groups}?q=${encodeURIComponent(trimmed)}` : routes.groups;
		const res = await fetch(url);
		if (res.ok) groupsList = await res.json();
	}

	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	// BUG FIXED HERE: this used to call only `search(query)`, so `refreshGroups` always ran with no
	// `q` and typing a group's own name found nothing — the search box read as "search everything"
	// while it only ever reached conversations. Both panes now see the same term.
	function onQueryInput() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			search(query);
			refreshGroups(query);
			// The third pane the same term has to reach. Without it the search narrows the clients
			// who have a thread and leaves every other client in the book standing underneath them.
			loadContacts(query);
		}, 250);
	}

	// The rail's three panels in ONE round trip, because they are one question: what else is true
	// about the client whose thread is open. Failure is silent and leaves the panels empty — this
	// surface is the CONVERSATION, and a side panel that could not be read must not put an error
	// over a thread that loaded perfectly (the posture the client record's own loader takes).
	// What each client's rail last answered, so returning to a conversation paints it immediately
	// instead of emptying and refilling. Keyed by the client, never by the conversation: it is the
	// CLIENT's advisers and owed work, and the same client has one thread. A plain Map, not
	// `$state` — it is a cache, and what the rail renders is `rail` below.
	const railCache = new Map<
		string,
		{
			client: RailClient | null;
			accounts: RailAccount[];
			notes: RailNote[];
			advisers: RailPerson[];
			requests: ApiRequest[];
			tasks: TaskRow[];
		}
	>();

	/** Writes the note, then puts it straight on the panel. The rail read is NOT repeated: the
	 *  endpoint answers with the created row, and re-reading would cost a round trip to learn
	 *  something already in hand. The cache is patched too, so returning to this client shows it. */
	async function addNote(body: string): Promise<string | null> {
		const contactUid = selection?.kind === 'conversation' ? selection.contactUid : null;
		if (!routes.notes || !contactUid) return 'Cannot write a note on this conversation.';
		const res = await fetch(routes.notes(contactUid), {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ body })
		});
		if (!res.ok) {
			return ((await res.json().catch(() => null))?.error as string) ?? "Couldn't save that note.";
		}
		const note = (await res.json()) as RailNote;
		// THE CACHE ALWAYS, THE PANEL ONLY IF IT IS STILL THEIRS. A save is a round trip, and the
		// reader can select another client inside it — putting the note on the panel unguarded
		// showed one client's note under another client's name.
		const cached = railCache.get(contactUid);
		if (cached) railCache.set(contactUid, { ...cached, notes: [note, ...cached.notes] });
		if (selection?.kind === 'conversation' && selection.contactUid === contactUid) {
			rail = { ...rail, notes: [note, ...rail.notes] };
		}
		return null;
	}

	/** Withdraws the note, then takes it off the panel and out of the cache — the mirror of
	 *  `addNote`, under the same guard that the selection may have moved during the round trip. */
	async function deleteNote(noteUid: string): Promise<string | null> {
		const contactUid = selection?.kind === 'conversation' ? selection.contactUid : null;
		if (!routes.note || !contactUid) return 'Cannot delete a note on this conversation.';
		const res = await fetch(routes.note(contactUid, noteUid), { method: 'DELETE' });
		if (!res.ok) {
			return (
				((await res.json().catch(() => null))?.error as string) ?? "Couldn't delete that note."
			);
		}
		const without = (notes: RailNote[]) => notes.filter((n) => n.uid !== noteUid);
		const cached = railCache.get(contactUid);
		if (cached) railCache.set(contactUid, { ...cached, notes: without(cached.notes) });
		if (selection?.kind === 'conversation' && selection.contactUid === contactUid) {
			rail = { ...rail, notes: without(rail.notes) };
		}
		return null;
	}

	async function loadRail(contactUid: string, contactId: number) {
		if (!routes.rail) return;
		// Whatever this client last answered goes up at once, and is then replaced by the fresh
		// read. Showing THIS client's last answer is not stale cross-contamination — the panels are
		// cleared on every selection change precisely so another client's never appears here.
		const cached = railCache.get(contactUid);
		if (cached) rail = cached;
		const res = await fetch(routes.rail(contactUid, contactId));
		if (!res.ok) return;
		const body = await res.json();
		const answer = {
			client: (body.client ?? null) as RailClient | null,
			accounts: (body.accounts ?? []) as RailAccount[],
			notes: (body.notes ?? []) as RailNote[],
			advisers: (body.advisers ?? []) as RailPerson[],
			requests: (body.requests ?? []) as ApiRequest[],
			tasks: (body.tasks ?? []) as TaskRow[]
		};
		railCache.set(contactUid, answer);
		// A slow answer must not land on whoever is selected NOW. Clicking A then B fires both
		// reads, and without this the slower one wins whichever client it was about.
		if (selection?.kind === 'conversation' && selection.contactUid === contactUid) rail = answer;
	}

	async function openConversation(id: number, uid: string) {
		messagesError = '';
		loadingThread = true;
		const res = await fetch(routes.conversation(uid));
		// STILL THE OPEN THREAD? Clicking down a list of 400 clients fires a read per click, and
		// nothing made the slower one lose: A's messages landed under B's name, beside B's rail
		// (which has guarded this since it was written), and A was marked read behind the
		// reader's back. Checked after every await, because each one is a place to be overtaken.
		const stillOpen = () => selection?.kind === 'conversation' && selection.uid === uid;
		if (!stillOpen()) return;
		if (!res.ok) {
			loadingThread = false;
			messagesError = "Couldn't load this conversation.";
			return;
		}
		const landed: ApiMessage[] = await res.json();
		if (!stillOpen()) return;
		messages = landed;
		loadingThread = false;
		// Only the CLIENT's messages count towards the watermark. The firm's own are read by
		// definition, and a watermark older than a reply you sent yourself would put "New" above
		// your own words. Read off the row as it stands, before the receipt below clears it.
		const seenUpTo = conversations.find((c) => c.id === id)?.staff_last_read_at ?? null;
		firstUnreadId =
			messages.find((m) => m.sender_kind !== 'staff' && (!seenUpTo || m.created_at > seenUpTo))
				?.id ?? null;
		await scrollToBottom();
		// THE RECEIPT IS A WRITE, and a read-only mount (the admin book) has it refused — after
		// which clearing the dot locally stated a read that never happened. Every other path to
		// this POST already asks `canWrite`; this one did not.
		if (!canWrite || !stillOpen()) return;
		await fetch(routes.read(uid), { method: 'POST' });
		noteRead(id);
		notifyUnreadChanged();
	}

	async function openGroup(uid: string) {
		groupFeedError = '';
		loadingThread = true;
		// A group carries no read watermark (`ApiGroup` has no `unread`), and a stale id from the
		// conversation last open would mark a run in this feed that nobody left off at.
		firstUnreadId = null;
		const res = await fetch(routes.group(uid));
		if (!res.ok) {
			loadingThread = false;
			groupFeedError = "Couldn't load this group.";
			return;
		}
		const body: { members: typeof groupMembers; messages: ApiGroupFeedMessage[] } =
			await res.json();
		// Same race as a conversation's: a slow group read must not paint over whatever is open
		// by the time it lands.
		if (!(selection?.kind === 'group' && selection.uid === uid)) return;
		groupMembers = body.members;
		groupFeed = body.messages;
		loadingThread = false;
		await scrollToBottom();
	}

	async function openGroupDialog() {
		groupDialogOpen = true;
		groupError = '';
		groupName = '';
		groupMemberIds = [];
		await ensureGroupPickerContacts();
	}

	/** Selecting a client nobody has messaged yet. The channel is opened on the click, exactly as
	 *  the client record's Messages section opens one on load — the adviser can always reach their
	 *  own book, and being that client's assigned adviser is not required
	 *  (see app.messaging.get_or_create_conversation_for_contact). */
	async function startConversation(contact: DirectoryRow) {
		if (opening !== null) return;
		opening = contact.contactId;
		directoryError = '';
		const res = await fetch(routes.conversations, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ contact_id: contact.contactId })
		});
		opening = null;
		if (!res.ok) {
			directoryError = "Couldn't open a conversation with that client.";
			return;
		}
		// THE SERVER'S OWN ROW, not a blank one built around it. `get_or_create` answers with the
		// EXISTING thread when there is one, carrying its last message, its time and its unread
		// flag — and this used to overwrite all three with nulls, so opening a client from the
		// directory half erased a conversation that was already there. Only the name is ours: the
		// create response has no contact_name.
		const conv = await res.json();
		conversations = [
			{ ...conv, contact_name: conv.contact_name ?? contact.name },
			...conversations.filter((c) => c.id !== conv.id)
		];
		selection = {
			kind: 'conversation',
			id: conv.id,
			uid: conv.uid,
			contactUid: conv.contact_uid,
			contactId: conv.contact_id
		};
	}

	async function createGroup(e: SubmitEvent) {
		e.preventDefault();
		const name = groupName.trim();
		if (!name || groupMemberIds.length === 0 || creatingGroup) return;
		creatingGroup = true;
		groupError = '';
		const res = await fetch(routes.groups, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ name, contact_ids: groupMemberIds.map(Number) })
		});
		creatingGroup = false;
		if (!res.ok) {
			groupError = (await res.json().catch(() => null))?.error ?? "Couldn't create that group.";
			return;
		}
		const created: { id: number; uid: string; name: string; created_at: string } = await res.json();
		groupsList = [
			{
				id: created.id,
				uid: created.uid,
				name: created.name,
				created_at: created.created_at,
				last_message: null,
				last_message_at: null
			},
			...groupsList
		];
		selection = { kind: 'group', id: created.id, uid: created.uid };
		groupDialogOpen = false;
	}

	/** The line a list row shows for a message — its words, or what it carried when it had
	 *  none. One derivation, because a send and an arrival must not word the same message
	 *  differently in the same list. */
	const previewOf = (body: string | null, attachments: { filename: string }[]) =>
		body ?? (attachments.length > 0 ? `Attachment: ${attachments[0]?.filename ?? 'file'}` : null);

	async function send(e: SubmitEvent) {
		e.preventDefault();
		if (!selection) return;
		const text = draft.trim();
		if ((!text && draftFiles.length === 0) || sending) return;
		sending = true;
		sendError = '';
		groupSendError = '';

		const isGroup = selection.kind === 'group';
		let res: Response;
		if (draftFiles.length > 0) {
			const body = new FormData();
			for (const file of draftFiles) body.append('file', file, file.name);
			body.append('text', text);
			res = await fetch(
				isGroup
					? routes.groupAttachments(selection.uid)
					: routes.conversationAttachments(selection.uid),
				{ method: 'POST', body }
			);
		} else {
			res = await fetch(
				isGroup ? routes.group(selection.uid) : routes.conversation(selection.uid),
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ text })
				}
			);
		}
		sending = false;
		if (!res.ok) {
			// EVERY failed send says so, a text-only one included: that branch set nothing and
			// reported nothing, so a message that did not go left the words sitting in the box with
			// no hint they had not been sent.
			const stated = ((await res.json().catch(() => null))?.error as string | undefined) ?? null;
			if (isGroup) groupSendError = stated ?? "Couldn't send that to the group.";
			else sendError = stated ?? 'Could not send that.';
			return;
		}
		// THE ROW MOVES WITH THE MESSAGE. The realtime handler patches the list when something
		// ARRIVES, and nothing patched it when something was SENT — so the firm's own reply left
		// the list showing the client's last line, at the client's last time, until a reload. Same
		// patch, same re-sort, same preview wording as an arrival.
		if (isGroup) {
			const batch: ApiGroupFeedMessage = await res.json();
			groupFeed = [...groupFeed, batch];
			const preview = previewOf(batch.body, batch.attachments);
			groupsList = [...groupsList]
				.map((g) =>
					g.uid === selection?.uid
						? { ...g, last_message: preview, last_message_at: batch.created_at }
						: g
				)
				.sort((x, y) => (y.last_message_at ?? '').localeCompare(x.last_message_at ?? ''));
		} else {
			const message: ApiMessage = await res.json();
			messages = [...messages, message];
			const preview = previewOf(message.body, message.attachments);
			conversations = conversations
				.map((c) =>
					c.id === message.conversation_id
						? { ...c, last_message: preview, last_message_at: message.created_at }
						: c
				)
				.sort((a, b) => (b.last_message_at ?? '').localeCompare(a.last_message_at ?? ''));
		}
		draft = '';
		draftFiles = [];
		await scrollToBottom();
	}

	// Live push while the inbox is mounted. The browser connects to its own portal; that BFF opens
	// the private API stream with the server-held bearer. EventSource reconnects automatically.
	// Every (re)open refetches the source of truth, because no ephemeral stream can replay what
	// happened while it was disconnected.
	$effect(() => {
		const events = new EventSource(routes.events);

		async function handleConversationMessage(payload: {
			conversation_id: number;
			message: ApiMessage;
		}) {
			const isOpen = selection?.kind === 'conversation' && payload.conversation_id === selection.id;
			// Being the open conversation isn't "read" by itself — only true if the thread was
			// actually scrolled to the bottom when this arrived (mirrors Slack/Teams: scrolled
			// up reading history, a new message stays unread until you scroll down to it) AND the
			// tab is actually visible AND focused: this component stays mounted (and its stream
			// connected) in a backgrounded tab, and a thread left scrolled to the bottom — its
			// default state — was auto-marking every live push read before a human ever saw it,
			// which is exactly why the nav dot never got a chance to light up. `document.hidden`
			// alone isn't enough: it only goes true when the TAB itself is switched away from or
			// the window minimised — this exact inbox left open in its own (background, unfocused)
			// window while the adviser works in a different app or window reports `hidden: false`
			// the whole time, so `document.hasFocus()` is the second half of "actually being
			// looked at".
			// An ABSENT scroll container counts as at-the-bottom, not as away from it. The empty
			// state renders a different element with no `bind:this`, so `messagesEl` is undefined
			// on a conversation with no messages yet — and the FIRST message into a fresh thread
			// therefore lit the unread dot while the reader was staring straight at it. There is
			// nothing to have scrolled away from when there is nothing to scroll.
			const seenImmediately =
				isOpen &&
				(!messagesEl || isNearBottom(messagesEl)) &&
				typeof document !== 'undefined' &&
				!document.hidden &&
				document.hasFocus();
			const known = conversations.some((c) => c.id === payload.conversation_id);
			if (!known) {
				// A conversation not yet in the local list (e.g. a client's very first-ever
				// message) — nothing to patch locally, so pull its row from the server instead.
				await search(query);
			} else {
				// Patch in place from the payload itself rather than refetching — a refetch
				// here would race the read receipt below and could put a just-cleared dot
				// back (the bug this replaced). Re-sort so the touched conversation moves to
				// the top, same ordering the server applies.
				const lastMessage = previewOf(payload.message.body, payload.message.attachments);
				conversations = conversations
					.map((c) =>
						c.id === payload.conversation_id
							? {
									...c,
									last_message: lastMessage,
									last_message_at: payload.message.created_at,
									unread: !seenImmediately
								}
							: c
					)
					.sort((a, b) => (b.last_message_at ?? '').localeCompare(a.last_message_at ?? ''));
			}
			if (isOpen) {
				messages = [...messages, payload.message];
				if (seenImmediately && canWrite) {
					await scrollToBottom();
					await fetch(routes.read(payload.message.conversation_uid), { method: 'POST' });
					noteRead(payload.conversation_id);
				}
			}
			notifyUnreadChanged();
		}

		async function handleGroupMessagePush(payload: {
			group_id: number;
			message: ApiGroupFeedMessage;
		}) {
			await refreshGroups();
			if (selection?.kind === 'group' && selection.id === payload.group_id) {
				// The sender's own send already appended this batch optimistically (see `send`) before
				// its broadcast echoes back on this same staff_key connection — without this check the
				// echo duplicates it, and the thread's keyed `{#each}` crashes on two entries sharing
				// one id.
				if (!groupFeed.some((m) => m.id === payload.message.id)) {
					groupFeed = [...groupFeed, payload.message];
					await scrollToBottom();
				}
			}
			// USUALLY staff-sent (doesn't move the unread definition at all), but occasionally a
			// client's own fulfilment via the group — cheap enough to just always recheck rather
			// than duplicate that classification here.
			notifyUnreadChanged();
		}

		async function handleMessage(event: MessageEvent) {
			const payload = JSON.parse(event.data);
			if (payload.type === 'resync') {
				await resync();
			} else if (payload.type === 'group_message') {
				await handleGroupMessagePush(payload);
			} else {
				await handleConversationMessage(payload);
			}
		}

		async function resync() {
			await Promise.all([search(query), refreshGroups()]);
			if (selection?.kind === 'conversation') {
				const current = selection;
				const response = await fetch(routes.conversation(current.uid));
				if (response.ok) {
					const seenImmediately =
						(!messagesEl || isNearBottom(messagesEl)) &&
						typeof document !== 'undefined' &&
						!document.hidden &&
						document.hasFocus();
					messages = await response.json();
					if (seenImmediately && canWrite) {
						await scrollToBottom();
						await fetch(routes.read(current.uid), { method: 'POST' });
						noteRead(current.id);
					}
				}
			} else if (selection?.kind === 'group') {
				await openGroup(selection.uid);
			}
			notifyUnreadChanged();
		}

		events.addEventListener('open', () => void resync());
		events.addEventListener('message', (event) => void handleMessage(event));

		return () => events.close();
	});

	// The half of the list the page load does not carry. Read once on mount — nothing reactive is
	// touched here, so it does not re-run — and afterwards only by the search box.
	$effect(() => {
		loadContacts('');
	});

	// A later `?conversation=` / `?group=` (the header search, while this inbox is already
	// mounted) has to move the selection: it is seeded once, above. Returns BEFORE reading the
	// lists once the asked-for thread is already open, so a read receipt rewriting `conversations`
	// cannot retrigger it — that loop is the one the selection's own comment describes.
	$effect(() => {
		const uid = openConversationUid;
		const groupUid = openGroupUid;
		if (uid) {
			if (selection?.kind === 'conversation' && selection.uid === uid) return;
			const row = conversations.find((c) => c.uid === uid);
			if (row) selection = conversationSelection(row);
			return;
		}
		if (groupUid) {
			if (selection?.kind === 'group' && selection.uid === groupUid) return;
			const group = groupsList.find((g) => g.uid === groupUid);
			if (group) selection = { kind: 'group', id: group.id, uid: group.uid };
		}
	});

	$effect(() => {
		threadSearch = '';
		conversationTab = 'chat';
		// Cleared FIRST, synchronously: the fetches below are awaited, so without this the previous
		// client's advisers and open requests stay on screen against the newly-selected one. The
		// THREAD goes with them, for the same reason — one client's messages standing under another
		// client's name is the worst thing this pane can say — and the skeleton takes its place
		// until the read lands.
		rail = { client: null, accounts: [], notes: [], advisers: [], requests: [], tasks: [] };
		messages = [];
		groupFeed = [];
		firstUnreadId = null;
		if (selection?.kind === 'conversation') {
			openConversation(selection.id, selection.uid);
			loadRail(selection.contactUid, selection.contactId);
		} else if (selection?.kind === 'group') {
			openGroup(selection.uid);
		}
	});
</script>

<!-- WHILE THE THREAD IS BEING READ. Not the "send a message to start the conversation" panel:
     that is a statement that this client has never written to the firm, and showing it for the
     half-second of every read said it about clients with a year of history. The shape is the
     thread's own — a run of bubbles, alternating sides — so what lands replaces it in place. -->
{#snippet threadSkeleton()}
	<!-- AT THE BOTTOM, where the messages it stands in for will be — a skeleton that sits at the
	     top and is replaced by a thread at the foot of the pane is a jump, not a placeholder. -->
	<div class="flex min-h-0 flex-1 flex-col overflow-hidden px-4 py-5" aria-hidden="true">
		<div class="mt-auto w-full space-y-5">
			{#each [0, 1, 2] as i (i)}
				<div class={cn('flex items-start gap-3', i === 1 && 'flex-row-reverse')}>
					<Skeleton class="size-7 shrink-0 rounded-full" />
					<div class={cn('flex min-w-0 flex-col gap-1.5', i === 1 && 'items-end')}>
						<Skeleton class="h-3 w-24" />
						<Skeleton class={cn('h-10 rounded-xl', i === 1 ? 'w-52' : 'w-64')} />
					</div>
				</div>
			{/each}
		</div>
	</div>
{/snippet}

{#snippet row(entry: DirectoryRow)}
	{@const conversation = entry.conversation}
	{@const selected =
		selection?.kind === 'conversation' && selection.id === (conversation?.id ?? -1)}
	<button
		type="button"
		onclick={() => {
			if (!conversation) return void startConversation(entry);
			selection = {
				kind: 'conversation',
				id: conversation.id,
				uid: conversation.uid,
				contactUid: conversation.contact_uid,
				contactId: conversation.contact_id
			};
		}}
		disabled={!conversation && !canWrite}
		aria-pressed={selected}
		aria-busy={opening === entry.contactId}
		class={cn(
			'flex w-full items-center gap-3 rounded-lg px-2 py-2.5 text-left transition-colors',
			'disabled:pointer-events-none disabled:opacity-50',
			// SELECTED IS THE BRAND TINT, on an inset pill. A full-bleed `bg-muted` band read as a
			// row that had lost its background rather than as the one being read, and it was the
			// same grey the hover uses — so the thread you are in looked like the row under the
			// pointer. This is the tint the filter pill above the list already carries.
			selected ? 'bg-brand-subtle text-brand-strong' : 'hover:bg-muted/60'
		)}
	>
		<ClientAvatar name={entry.name ?? 'Unknown'} kind={entry.kind} size="md" class="shrink-0" />
		<div class="min-w-0 flex-1">
			<p class="truncate font-medium">{entry.name ?? 'Unknown'}</p>
			<p class="text-muted-foreground truncate text-sm">
				{conversation?.last_message ?? 'No messages yet'}
			</p>
		</div>
		<div class="flex shrink-0 flex-col items-end gap-1">
			<span class="text-muted-foreground text-xs">
				{formatListTime(conversation?.last_message_at ?? null, now())}
			</span>
			{#if conversation?.unread}
				<span class="bg-primary size-2 rounded-full" aria-hidden="true"></span>
			{/if}
		</div>
	</button>
{/snippet}

{#snippet groupRow(group: ApiGroup)}
	<button
		type="button"
		onclick={() => (selection = { kind: 'group', id: group.id, uid: group.uid })}
		aria-pressed={selection?.kind === 'group' && selection.id === group.id}
		class={cn(
			'flex w-full items-center gap-3 rounded-lg px-2 py-2.5 text-left transition-colors',
			selection?.kind === 'group' && selection.id === group.id
				? 'bg-brand-subtle text-brand-strong'
				: 'hover:bg-muted/60'
		)}
	>
		<!-- SEVERAL PEOPLE, not one. A group listed beside clients with the same person glyph was
		     indistinguishable from an individual in the same column. -->
		<ClientAvatar name={group.name} kind="group" size="md" class="shrink-0" />
		<div class="min-w-0 flex-1">
			<p class="truncate font-medium">{group.name}</p>
			<p class="text-muted-foreground truncate text-sm">
				{group.last_message ?? 'No messages yet'}
			</p>
		</div>
		<div class="flex shrink-0 flex-col items-end gap-1">
			<span class="text-muted-foreground text-xs">
				{formatListTime(group.last_message_at, now())}
			</span>
		</div>
	</button>
{/snippet}

<!-- The surface fills the content area and the negative margin cancels the shell's padding, so
     TWO CARDS, side by side: the register of clients to pick FROM, and the conversation itself.
     Both were full-bleed columns running to the viewport edge, which read as two halves of a
     second application rather than as panels of this one.

     The RAIL stays inside the conversation's card, divided from the thread by its own hairline
     rather than lifted into a third card: it is context about the thread it sits beside, and the
     two are one surface — the same relationship they have on the client record.

     The frame cancels the shell's padding and re-applies it as its own, so the cards sit on the
     normal content inset with one gap between them. -->
<div
	class="-m-4 flex h-[calc(100svh-var(--topbar-h,4rem))] flex-col gap-4 p-4 md:-m-6 md:p-6 lg:flex-row"
>
	<!-- Conversation list -->
	{#if showDirectory}
		<aside
			class="bg-card flex min-h-0 flex-col overflow-hidden rounded-xl shadow-xs lg:w-72 lg:shrink-0"
		>
			<div class="px-4 py-3">
				<div class="relative">
					<SearchIcon
						class="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
					/>
					<Input
						bind:value={query}
						oninput={onQueryInput}
						placeholder="Search direct messages..."
						class="pl-9"
					/>
				</div>
				<!-- Pill toggle, not a `FilterSelect`: one boolean over one field doesn't need a
				     dropdown, and this idiom (aria-pressed + the brand-subtle/outline pair) is the
				     house one for a single on/off filter — see investment/widget-toolbar.svelte's
				     category pills. Scoped to Direct messages only; Groups has no `unread` to filter
				     by (see `visibleRows`' own comment), so no toggle is offered there. -->
				<button
					type="button"
					onclick={() => (unreadOnly = !unreadOnly)}
					aria-pressed={unreadOnly}
					class={cn(
						'focus-visible:ring-ring mt-2 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none',
						unreadOnly
							? 'border-brand-line bg-brand-subtle text-brand-strong'
							: 'border-border text-muted-foreground hover:bg-muted/60'
					)}
				>
					Unread only
				</button>
			</div>

			<div class="flex-1 overflow-y-auto px-2 py-2">
				<!-- NO "new message" button. The band below is every client the reader may write to,
				     so the thing a "+" used to open — pick a client, start a thread — is the list
				     itself; clicking a client with no thread opens one. -->
				<!-- ONLY WHEN THERE IS A SECOND SECTION TO TELL IT FROM. Groups is hidden by default,
				     so this column is every direct message there is — a heading announcing the only
				     thing on screen is a row of chrome between the search box and the first client.
				     With Groups shown it earns its place, and it is sticky so a reader scrolling a
				     book of 400 knows which list they are in. -->
				{#if showGroups}
					<div
						class="bg-card/85 sticky top-0 z-10 flex items-center gap-2 px-2 py-2 backdrop-blur-sm"
					>
						<h2
							class="text-muted-foreground text-[0.6875rem] font-semibold tracking-wider uppercase"
						>
							Direct messages
						</h2>
						<span class="text-muted-foreground/70 text-[0.6875rem] font-medium tabular-nums">
							{visibleRows.length}
						</span>
					</div>
				{/if}
				{#if directoryError}
					<p class="text-destructive px-2 py-2 text-xs">{directoryError}</p>
				{/if}
				{#each visibleRows as entry (entry.contactId)}
					{@render row(entry)}
				{/each}
				{#if visibleRows.length === 0}
					<!-- Two different reasons the pane is empty: the search itself matched nothing (the
					     original message), or it matched something and "Unread only" then hid it all —
					     worth its own words rather than one caption doing for both. -->
					<p class="text-muted-foreground px-4 py-3 text-center text-sm">
						{directory.length === 0 ? 'No clients match.' : 'No unread conversations.'}
					</p>
				{/if}

				<!-- GROUPS ARE NOT OFFERED HERE. The section, its `+` and its empty state are hidden
				     rather than deleted: everything behind them is live and tested — the API, the
				     fan-out into each member's own conversation, request-to-group, the feed and its
				     composer below, the realtime handling — and a firm that wants the shortcut gets
				     it back by rendering `groupRow` over `groupsList` again.
				     `showGroups` is the one switch. Default false: an empty "Groups / No groups yet"
				     block is a standing invitation to a feature nobody here uses, and it reads as
				     something broken rather than as something unused. -->
				{#if showGroups}
					<div
						class="bg-card/85 sticky top-0 z-10 mt-3 flex items-center justify-between px-4 py-2 backdrop-blur-sm"
					>
						<h2
							class="text-muted-foreground text-[0.6875rem] font-semibold tracking-wider uppercase"
						>
							Groups
						</h2>
						{#if canWrite}
							<button
								type="button"
								aria-label="New group"
								onclick={openGroupDialog}
								class="text-muted-foreground hover:text-foreground hover:bg-muted rounded-md p-1"
							>
								<PlusIcon class="size-4" />
							</button>
						{/if}
					</div>
					{#each groupsList as group (group.id)}
						{@render groupRow(group)}
					{/each}
					{#if groupsList.length === 0}
						<p class="text-muted-foreground px-4 py-3 text-center text-sm">No groups yet.</p>
					{/if}
				{/if}
			</div>
		</aside>
	{/if}

	<!-- Conversation: the thread, and the rail beside it behind one hairline. CARDED, like the
	     list beside it — and the client record's Messages tab wears the same card, so the same
	     conversation has the same surface whichever door you open it through. -->
	<div
		class="bg-card flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-xl shadow-xs lg:flex-row"
	>
		<section class="relative flex min-w-0 flex-1 flex-col">
			{#if selectedConversation}
				<ConversationPanels
					bind:tab={conversationTab}
					bind:search={threadSearch}
					{messages}
					download={routes.download}
				>
					<!-- NO HEADER over the thread. The rail beside it already leads with the client — their
				     face, their name and the road to their record — and the list on the left has them
				     selected, so a third statement of the same name was a band of chrome between the
				     reader and the conversation. The TAB STRIP is what sits there instead. -->
					{#if loadingThread && messages.length === 0}
						{@render threadSkeleton()}
					{:else if messages.length === 0 && !messagesError}
						<!-- `justify-end`: the prompt sits directly above the composer rather than floating in
				     the middle of an empty pane. A conversation grows upwards from the box you type
				     into, so the invitation to start one belongs where the first message will appear. -->
						<div
							class="flex flex-1 flex-col items-center justify-end gap-4 px-6 pt-10 pb-4 text-center"
						>
							<ClientAvatar name={selectedConversation.contact_name ?? 'Unknown'} size="lg" />
							<div>
								<p class="font-semibold">{selectedConversation.contact_name ?? 'Unknown'}</p>
								<p class="text-muted-foreground mt-1 text-sm">
									Send a message to start the conversation.
								</p>
							</div>
						</div>
					{:else}
						<MessageThread
							{messages}
							clientName={() => selectedConversation?.contact_name ?? 'Unknown'}
							{firstUnreadId}
							search={threadSearch}
							download={routes.download}
							error={messagesError}
							bind:scrollEl={messagesEl}
							onscroll={onMessagesScroll}
						/>
					{/if}

					<!-- ONE composer, docked at the foot of the pane in BOTH states and the full width of the
			     thread it answers: the reply box does not move when the first message lands, and it is
			     not half the width of the messages above it. A reader with `messages:read` and no
			     write gets the thread with no box they cannot send from. -->
					{#if canWrite}
						<div class="w-full px-4 pb-4">
							{@render dmComposer()}
						</div>
					{/if}
				</ConversationPanels>
			{:else if selectedGroup}
				<ConversationPanels
					bind:tab={conversationTab}
					bind:search={threadSearch}
					messages={groupFeed}
					download={routes.download}
				>
					<header class="flex items-center gap-3 px-4 py-3">
						<ClientAvatar name={selectedGroup.name} kind="group" size="md" class="shrink-0" />
						<div class="min-w-0 flex-1">
							<p class="truncate font-semibold leading-tight">{selectedGroup.name}</p>
							<p class="text-muted-foreground text-xs">
								Group · {groupMembers.length}
								{groupMembers.length === 1 ? 'member' : 'members'}
							</p>
						</div>
					</header>

					{#if loadingThread && groupFeed.length === 0}
						{@render threadSkeleton()}
					{:else if groupFeed.length === 0 && !groupFeedError}
						<div
							class="flex flex-1 flex-col items-center justify-end gap-4 px-6 pt-10 pb-4 text-center"
						>
							<ClientAvatar name={selectedGroup.name} kind="group" size="lg" />
							<div>
								<p class="font-semibold">{selectedGroup.name}</p>
								<p class="text-muted-foreground mt-1 text-sm">
									Send a message to reach every member of this group.
								</p>
							</div>
						</div>
					{:else}
						<!-- The same thread a direct conversation draws — a group feed is those same messages
				     merged, so the only differences it earns are the "To …" line each send carries and
				     the absence of a "New" bar: a group holds no read watermark of its own (`ApiGroup`
				     has no `unread`), so there is no point to mark. -->
						<MessageThread
							messages={groupFeed}
							clientName={(entry) => entry.recipients[0]?.contact_name ?? 'Client'}
							search={threadSearch}
							download={routes.download}
							error={groupFeedError}
							subline={groupRecipients}
							bind:scrollEl={messagesEl}
							onscroll={onMessagesScroll}
						/>
					{/if}

					{#if canWrite}
						<div class="w-full px-4 pb-4">
							{@render groupComposer()}
						</div>
					{/if}
				</ConversationPanels>
			{:else}
				<div class="flex flex-1 items-center justify-center">
					<p class="text-muted-foreground text-sm">No conversations yet.</p>
				</div>
			{/if}
		</section>
		{#if selectedConversation || selectedGroup}
			<!-- The same rail the client record carries: who is on the conversation, every file that
			     has crossed it, and what the client still owes. A GROUP names its members and nothing
			     owed — "waiting on the client" cannot mean one thing across several clients. -->
			<MessageRail
				client={selectedConversation ? rail.client : null}
				accounts={selectedConversation ? rail.accounts : []}
				clientHref={selectedConversation && links?.client
					? links.client(selectedConversation.contact_uid)
					: null}
				people={railPeople}
				peopleNote={selectedConversation ? 'No adviser is assigned to this client.' : undefined}
				notes={selectedConversation ? rail.notes : []}
				onAddNote={selectedConversation && routes.notes ? addNote : undefined}
				onDeleteNote={selectedConversation && routes.note ? deleteNote : undefined}
				requests={selectedConversation ? rail.requests : []}
				tasks={selectedConversation ? rail.tasks : []}
				{showTasks}
				{links}
			/>
		{/if}
	</div>
</div>

<Dialog.Root bind:open={groupDialogOpen}>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>New group</Dialog.Title>
			<Dialog.Description>
				A saved shortcut to related clients (a household or joint account) — sending to a group
				messages every member individually; they never see that they've been grouped.
			</Dialog.Description>
		</Dialog.Header>
		<form onsubmit={createGroup} class="space-y-4">
			<div class="space-y-2">
				<Label for="group-name">Group name</Label>
				<Input id="group-name" bind:value={groupName} placeholder="The Smith Household" autofocus />
			</div>
			<div class="space-y-2">
				<Label for="group-members">Client(s)</Label>
				<MultiSelect
					id="group-members"
					options={contactOptions}
					bind:value={groupMemberIds}
					placeholder="Search clients..."
					searchPlaceholder="Search clients..."
				/>
			</div>
			{#if groupError}
				<p class="text-destructive text-sm">{groupError}</p>
			{/if}
			<Dialog.Footer>
				<Button type="button" variant="outline" onclick={() => (groupDialogOpen = false)}>
					Cancel
				</Button>
				<Button
					type="submit"
					disabled={!groupName.trim() || groupMemberIds.length === 0 || creatingGroup}
				>
					{creatingGroup ? 'Creating…' : 'Create group'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>

<!-- Who a group send actually reached, inside the bubble it describes rather than once per run:
     a run is one sender within a few minutes, and two sends in one can name different members, so
     the line has to travel with its own message. Only on your OWN sends — a client's group-tagged
     fulfilment came to the firm, not to a list. -->
{#snippet groupRecipients(entry: ApiGroupFeedMessage)}
	{#if entry.sent_by_me}
		<p class="text-muted-foreground text-[11px]">
			To {entry.recipients.map((r) => r.contact_name ?? 'Client').join(', ')}
		</p>
	{/if}
{/snippet}

{#snippet dmComposer()}
	<MessageComposer
		bind:draft
		bind:files={draftFiles}
		error={sendError}
		{sending}
		submit={send}
		onRequest={canRequestDocuments && selectedConversation
			? () =>
					selectedConversation &&
					onRequest?.({
						kind: 'conversation',
						contact_id: selectedConversation.contact_id,
						name: selectedConversation.contact_name
					})
			: undefined}
	/>
{/snippet}

{#snippet groupComposer()}
	<!-- Same composer as a direct message — an attachment is just a file fanned out like the text
	     is, and a request is the same ask raised against every member. -->
	<MessageComposer
		bind:draft
		bind:files={draftFiles}
		placeholder="Message everyone in this group... (Ctrl+Enter to send)"
		error={groupSendError}
		{sending}
		submit={send}
		sendLabel="Send to group"
		requestLabel="Request a document from every member"
		onRequest={canRequestDocuments && selectedGroup
			? () =>
					selectedGroup &&
					onRequest?.({
						kind: 'group',
						id: selectedGroup.id,
						uid: selectedGroup.uid,
						name: selectedGroup.name,
						refresh: () => openGroup(selectedGroup.uid)
					})
			: undefined}
	/>
{/snippet}
