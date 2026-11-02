<script lang="ts" generics="T extends ThreadMessage">
	/**
	 * The conversation pane's HEADER ROW — Chat and Files, and the search box — and the Files panel
	 * behind it.
	 *
	 * ONE RESERVED ROW. The tabs and the box are `shrink-0` inside a flex column, and the thread
	 * below them is the only thing that scrolls, so nothing ever slides under either. A header that
	 * a long conversation can push off the top is a header you cannot use at the moment you want it.
	 *
	 * ONLY TWO TABS, because only two things here are views of the CONVERSATION: what was said, and
	 * what was sent. Everything else the rail used to stack — the client's accounts, notes, the
	 * people on the thread and the work outstanding — is about the CLIENT, and lives in the rail as
	 * collapsible sections.
	 */
	import { EmptyState, cn } from '@graphene/ui';
	import { SEARCH_BOX_CLASS } from '../listing/filters.js';
	import type { Snippet } from 'svelte';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import PaperclipIcon from '@lucide/svelte/icons/paperclip';
	import MessageSquareIcon from '@lucide/svelte/icons/message-square';
	import SearchIcon from '@lucide/svelte/icons/search';
	import XIcon from '@lucide/svelte/icons/x';
	import { attachmentIcon, formatMessageTime } from './messages.js';
	import { formatBytes } from '../../../bytes.js';
	// `ThreadMessage` is the `generics=` constraint above; svelte-eslint-parser does not look
	// inside that attribute, so the rule has to be told.
	// eslint-disable-next-line @typescript-eslint/no-unused-vars
	import type { ConversationTab, ThreadMessage } from './messages.js';

	let {
		tab = $bindable('chat'),
		search = $bindable(''),
		messages,
		download,
		children
	}: {
		tab?: ConversationTab;
		/** Bound, so a surface can clear it when the selection changes — otherwise a term typed on
		 *  one client silently narrows the next one's thread to nothing. */
		search?: string;
		messages: T[];
		download: (conversationUid: string, attachmentUid: string) => string;
		/** The Chat tab: the thread and its composer, which stay with the caller because what a
		 *  send POSTs to differs per surface. */
		children: Snippet;
	} = $props();

	// Every file that has crossed this conversation, newest first. The MESSAGES are reversed, then
	// flattened — not the flattened list: several files ride one message (0123), and reversing
	// afterwards would put that message's own files in the reverse of the order they were sent in.
	const chatFiles = $derived(
		[...messages].reverse().flatMap((m) =>
			m.attachments.map((att) => ({
				att,
				conversationUid: m.conversation_uid,
				sentAt: m.created_at,
				mine: m.sent_by_me === true
			}))
		)
	);

	/** The box is CLOSED until asked for, like the tabs beside it. Closing CLEARS the term: a
	 *  collapsed box over a filtered thread is a thread missing messages for no reason the reader
	 *  can see, which is the one state this must not leave behind. */
	let searchOpen = $state(false);
	function closeSearch() {
		searchOpen = false;
		search = '';
	}

	/** IT FOLDS BACK WHEN THE READER LEAVES IT — an empty box left open is the control still
	 *  spending the row it was made a button to stop spending. With a term typed it STAYS: closing
	 *  clears, and clicking a result in the thread it filtered must not throw that filter away. */
	function onSearchBlur(event: FocusEvent & { currentTarget: HTMLElement }) {
		if (search.trim()) return;
		const next = event.relatedTarget;
		if (next instanceof Node && event.currentTarget.contains(next)) return;
		searchOpen = false;
	}

	const TABS = $derived([
		{ id: 'chat' as const, label: 'Chat', icon: MessageSquareIcon, count: null as number | null },
		{ id: 'files' as const, label: 'Files', icon: PaperclipIcon, count: chatFiles.length }
	]);
</script>

<div class="flex min-h-0 min-w-0 flex-1 flex-col">
	<!-- RESERVED: `shrink-0`, so the thread below scrolls under nothing. No rule under it — the
	     controls sit in their own space and a line across the pane was one more edge to read. -->
	<div class="flex shrink-0 items-center gap-1 px-2 py-1.5">
		<div class="flex items-center gap-1" role="tablist">
			{#each TABS as t (t.id)}
				<button
					type="button"
					role="tab"
					aria-selected={tab === t.id}
					onclick={() => (tab = t.id)}
					class={cn(
						'focus-visible:ring-ring/50 flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm font-medium transition-colors outline-none focus-visible:ring-2',
						tab === t.id
							? 'bg-muted text-foreground'
							: 'text-muted-foreground hover:text-foreground'
					)}
				>
					<t.icon class="size-3.5 shrink-0" />
					{t.label}
					{#if t.count !== null && t.count > 0}
						<span class="text-muted-foreground text-[11px] font-semibold tabular-nums">
							{t.count}
						</span>
					{/if}
				</button>
			{/each}
		</div>
		<!-- A BUTTON THAT BECOMES A BOX, the same shape AND the same wording as the tabs beside it:
		     icon plus label, because a lone magnifier in a row of two labelled controls reads as a
		     decoration rather than as the third thing you can press. A permanent input here was a
		     field competing with both tabs for the reader's eye on every conversation, most of
		     which nobody searches. -->
		{#if searchOpen}
			<!-- `SEARCH_BOX_CLASS` — the same 24rem cap every search box in the book takes, so this
			     one is a search box rather than a field stretched across whatever row it lands in. -->
			<div class={cn('relative ms-1', SEARCH_BOX_CLASS)} onfocusout={onSearchBlur}>
				<SearchIcon
					class="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
				/>
				<!-- svelte-ignore a11y_autofocus -->
				<input
					bind:value={search}
					type="search"
					autofocus
					placeholder="Search this conversation..."
					aria-label="Search this conversation"
					onkeydown={(event) => event.key === 'Escape' && closeSearch()}
					class="border-input bg-background focus-visible:ring-ring h-9 w-full rounded-md border px-2 pr-9 pl-9 text-sm focus-visible:ring-2 focus-visible:ring-inset focus-visible:outline-none [&::-webkit-search-cancel-button]:appearance-none"
				/>
				<button
					type="button"
					onclick={closeSearch}
					aria-label="Close search"
					class="text-muted-foreground hover:text-foreground absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5"
				>
					<XIcon class="size-4" />
				</button>
			</div>
		{:else}
			<button
				type="button"
				onclick={() => (searchOpen = true)}
				aria-label="Search this conversation"
				class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 flex items-center gap-1.5 rounded-md px-2.5 py-1 text-sm font-medium transition-colors outline-none focus-visible:ring-2"
			>
				<SearchIcon class="size-3.5 shrink-0" />
				Search
			</button>
		{/if}
	</div>

	{#if tab === 'chat'}
		{@render children()}
	{:else}
		<div class="min-h-0 flex-1 overflow-y-auto p-4">
			{#if chatFiles.length === 0}
				<EmptyState
					icon={PaperclipIcon}
					title="No files yet"
					description="Anything sent or received on this conversation is collected here."
				/>
			{:else}
				<ul class="space-y-1">
					{#each chatFiles as file (file.att.uid)}
						{@const Glyph = attachmentIcon(file.att)}
						<li>
							<a
								href={download(file.conversationUid, file.att.uid)}
								download={file.att.filename}
								class="hover:bg-muted flex items-center gap-3 rounded-lg px-2 py-2"
							>
								<span
									class="bg-muted text-muted-foreground flex size-8 shrink-0 items-center justify-center rounded-lg"
								>
									<Glyph class="size-4" />
								</span>
								<span class="min-w-0 flex-1">
									<span class="block truncate text-sm font-medium" title={file.att.filename}>
										{file.att.filename}
									</span>
									<span class="text-muted-foreground block truncate text-xs">
										{formatBytes(file.att.size_bytes)} · {file.mine ? 'Sent' : 'Received'}
										{formatMessageTime(file.sentAt)}
									</span>
								</span>
								<DownloadIcon class="text-muted-foreground size-4 shrink-0" />
							</a>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	{/if}
</div>
