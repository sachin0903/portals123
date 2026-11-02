<script lang="ts">
	// The client's chat with their firm. Drawn on the SAME thread and composer the platform uses
	// (`@graphene/ui/book/messages`), in their `viewer="client"` register: this page used to carry
	// its own 500-line copy — a face, a name and a time over every single message, no bubbles, no
	// grouping — and so the improvements that landed on the staff side (runs, day rules, the "New"
	// bar, one card shape for every file) never reached the people the product is for. What is
	// client-specific stays here: who the thread is with (the adviser header), the fulfil-a-request
	// dialog, and the portal proxy paths the browser POSTs to.
	import {
		Avatar,
		Button,
		Dialog,
		EmptyState,
		FileDropzone,
		LoadErrorBanner,
		PersonAvatar
	} from '@graphene/ui';
	import { MessageComposer, MessageThread } from '@graphene/ui/book/messages';
	import { invalidate, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { ATTACHMENT_ACCEPT, type ApiRequestItem } from '$lib/messaging';
	import { backFor } from '$lib/nav';
	import Building2Icon from '@lucide/svelte/icons/building-2';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import UserRoundIcon from '@lucide/svelte/icons/user-round';

	let { data } = $props();
	const adviser = $derived(data.adviser);
	const myName = $derived(data.contacts.find((c) => c.id === data.activeContactId)?.name ?? 'You');
	// Who the thread is with (`$lib/adviser`): a named adviser, or the FIRM when nobody is
	// assigned (`kind: 'firm'`) — then the firm's whole name, never its "first name".
	const isFirm = $derived(adviser?.kind === 'firm');
	const firstName = $derived(
		adviser ? (isFirm ? adviser.name : adviser.name.split(/\s+/)[0]) : null
	);
	// Below `lg` this page is the screen (`immersive`, +page.server.ts) and the shell's top bar is
	// gone, so the way back the shell would have drawn there is drawn HERE, in the conversation's
	// own header — the same target the shell resolves for every sub page ($lib/nav::backFor).
	const back = $derived(backFor(page.url.pathname, page.data.branding?.pages));

	// The shared thread decides "mine" from `sent_by_me`, the server's answer for a staff reader
	// who may be one of several colleagues on a thread. A client is the only client on theirs, so
	// `sender_kind` IS the answer — and a live push omits the flag, which must still read as theirs.
	const messages = $derived(
		data.messages.map((m) => ({ ...m, sent_by_me: m.sender_kind === 'client' }))
	);
	const isEmpty = $derived(messages.length === 0);

	// Where the "New" bar goes: the first of the FIRM's messages after the client's own read
	// watermark. Computed once from the first render — the effect below marks the thread read
	// straight away, and a derived value would erase the bar as it did so.
	const firstUnreadId = (() => {
		const seenUpTo = data.conversation?.client_last_read_at ?? null;
		const first = data.messages.find(
			(m) => m.sender_kind === 'staff' && (!seenUpTo || m.created_at > seenUpTo)
		);
		return first?.id ?? null;
	})();

	let draft = $state('');
	let draftFiles = $state<File[]>([]);
	let sendError = $state('');
	let sending = $state(false);
	let threadEl: HTMLDivElement | undefined = $state();

	function scrollToBottom() {
		threadEl?.scrollTo({ top: threadEl.scrollHeight });
	}

	// The tap-to-upload card a pending request renders as — it opens this dialog rather than
	// staging into the composer, since it fulfils a SPECIFIC request_item (the composer's own
	// paperclip flow has no such target). Same dropzone-and-send shape as /requests' upload dialog.
	let fulfillOpen = $state(false);
	let fulfillTarget = $state<ApiRequestItem | null>(null);
	let fulfillFiles = $state<File[]>([]);
	let fulfillError = $state('');
	let fulfilling = $state(false);

	function openFulfil(item: ApiRequestItem) {
		fulfillTarget = item;
		fulfillFiles = [];
		fulfillError = '';
		fulfillOpen = true;
	}

	async function submitFulfilment() {
		if (fulfilling || !fulfillTarget) return;
		if (!fulfillFiles.length) {
			fulfillError = 'Choose a file.';
			return;
		}
		fulfilling = true;
		const body = new FormData();
		body.append('file', fulfillFiles[0], fulfillFiles[0].name);
		body.append('text', '');
		body.append('request_item_uid', fulfillTarget.uid);
		if (data.activeContactId) body.append('contact_id', String(data.activeContactId));
		const res = await fetch('/messages/attachments', { method: 'POST', body });
		fulfilling = false;
		if (!res.ok) {
			fulfillError = (await res.json().catch(() => null))?.error ?? 'Could not send that.';
			return;
		}
		fulfillOpen = false;
		await refreshThread();
		queueMicrotask(scrollToBottom);
	}

	async function send(e: SubmitEvent) {
		e.preventDefault();
		const text = draft.trim();
		if ((!text && draftFiles.length === 0) || sending) return;
		sending = true;
		sendError = '';

		if (draftFiles.length === 0) {
			const res = await fetch('/messages/send', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ text, contactId: data.activeContactId })
			});
			sending = false;
			if (!res.ok) {
				sendError = (await res.json().catch(() => null))?.error ?? 'Could not send that.';
				return;
			}
			draft = '';
			await refreshThread();
			queueMicrotask(scrollToBottom);
			return;
		}

		// ONE message carrying every staged file — a repeated `file` part, which is what the
		// endpoint's `list[UploadFile]` reads. This used to be a loop sending one message per file,
		// because 0081 made `messaging.attachments.message_id` UNIQUE; 0123 dropped that index and
		// the adviser side already sends the whole set at once. The loop was not merely dated: a
		// failure part-way through left some files sent and some staged, so "three documents" landed
		// as three separate bubbles that the adviser had to read as one.
		const body = new FormData();
		for (const file of draftFiles) body.append('file', file, file.name);
		body.append('text', text);
		if (data.activeContactId) body.append('contact_id', String(data.activeContactId));
		const res = await fetch('/messages/attachments', { method: 'POST', body });
		sending = false;
		if (!res.ok) {
			sendError = (await res.json().catch(() => null))?.error ?? 'Could not send that.';
			// Everything stays staged: one request means it either all went or none of it did, so
			// there is nothing to drop and nothing to send again by hand.
			return;
		}
		draftFiles = [];
		draft = '';
		await refreshThread();
		queueMicrotask(scrollToBottom);
	}

	// Re-read THIS page's loader only (`depends('app:messages')`), never the whole tree.
	const refreshThread = () => invalidate('app:messages');

	// The stream is keyed on a PRIMITIVE. `$derived` only notifies when the value changes, so a
	// reload of `data` (after a push, after a send) leaves the effect below alone. Reading
	// `data.conversation` directly did not: every reload handed the effect a new object, it closed
	// the EventSource and opened another, and the new connection's `open` reloaded again. Measured
	// at 138 connects, 138 reloads and 276 mark-read POSTs in fifteen seconds from one tab.
	// `0` stands in for "a conversation with no active contact"; contact ids are positive.
	const streamContactId = $derived(data.conversation ? (data.activeContactId ?? 0) : null);

	// Mark the thread read as soon as it's open, and open a same-origin event stream for the
	// adviser's replies. The portal holds the API bearer and proxies the private stream; no API
	// address or realtime ticket reaches browser code.
	$effect(() => {
		const key = streamContactId;
		if (key === null) return;
		const contactId = key === 0 ? null : key;
		const readBody = JSON.stringify({ contactId });
		const markRead = () =>
			fetch('/messages/read', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: readBody
			});
		const refresh = () => {
			void refreshThread();
			void markRead();
		};
		void markRead();
		const query = contactId === null ? '' : `?contact_id=${encodeURIComponent(contactId)}`;
		const events = new EventSource(`/messages/events${query}`);
		// Re-read on every RECONNECT as well as every push: SSE has no durable cursor, so the REST
		// thread remains the source of truth for anything delivered while the stream was down. The
		// first `open` is not a reconnect — the loader that rendered this page just read the thread.
		let opened = false;
		events.addEventListener('open', () => {
			if (opened) refresh();
			opened = true;
		});
		events.addEventListener('message', refresh);
		return () => events.close();
	});

	// Land on the latest message when the thread first renders; `scrollToBottom` used to run only
	// after a send, so a returning client opened at the oldest message.
	let scrolledOnLoad = false;
	$effect(() => {
		if (threadEl && !scrolledOnLoad && messages.length > 0) {
			scrolledOnLoad = true;
			scrollToBottom();
		}
	});
</script>

<svelte:head><title>Messages · {page.data.appName ?? 'Graphene'}</title></svelte:head>

{#if data.conversation}
	<!-- Below `lg` the page is IMMERSIVE (+page.server.ts → ClientShell): no top bar, no column
	     padding, so the conversation is simply the viewport (`h-dvh`) and draws its own header.
	     From `lg` the shell's bar and padding are back and the card sits UNDER the back row
	     (`--shell-back-h`) in the content column, cancelling only the column's bottom padding
	     (`-mb-10`) so it can end a 1.5rem gutter above the viewport edge. -->
	<div
		class="relative flex h-dvh w-full flex-col lg:-mb-10 lg:h-[calc(100svh-4rem-2.5rem-var(--shell-back-h,0px))] lg:pb-6"
	>
		<!-- ONE surface for the whole conversation — who it is with, what has been said, the box
		     you type into — the way every messaging app a client already uses draws it, and the
		     way the staff surfaces card theirs. The thread used to float on the page canvas with
		     nothing framing it. The card takes the shell's whole content column, not a narrower
		     reading measure: a chat is not an article, and a 48rem card centred in a 72rem column
		     left a margin either side that read as the page not being used. Line length is the
		     bubbles' own business (`max-w-[42rem]` each). -->
		<section
			class="glass-panel flex min-h-0 flex-1 flex-col overflow-hidden max-lg:rounded-none max-lg:border-0 lg:rounded-2xl"
			aria-label="Conversation"
		>
			<!-- WHO this thread is with, pinned above it. A chat without a name at the top is a
			     list of your own words. It is not a link: there is no adviser page to open, and
			     email and phone live on the adviser block wherever a firm places one. Below `lg`
			     this row IS the top bar — the shell's is hidden — so it leads with the same
			     "‹ Home" the shell draws on every other sub page and pays the status bar's safe
			     area. -->
			<div
				class="flex shrink-0 items-center gap-2 border-b py-2 pr-2 pl-3 max-lg:pt-[calc(0.5rem+env(safe-area-inset-top))] lg:px-3"
			>
				{#if back}
					<a
						href={back.href}
						class="glass text-muted-foreground hover:text-foreground tap-target inline-flex h-9 shrink-0 items-center gap-0.5 rounded-full pr-3.5 pl-2 text-sm font-medium transition-colors lg:hidden"
					>
						<ChevronLeftIcon class="size-5 shrink-0" aria-hidden="true" />
						<span class="max-w-24 truncate">{back.label}</span>
					</a>
				{/if}
				{#if adviser}
					<div class="flex min-w-0 flex-1 items-center gap-3 px-2 py-1.5">
						<!-- The firm's headshot when it has one; otherwise the SAME disc the thread below
					     draws beside their replies (`PersonAvatar` is one hue per person), so the
					     face at the top is recognisably the face in the conversation. -->
						{#if isFirm}
							<!-- The FIRM is the recipient: its icon (a mark, contained — not a face,
							     cropped), else a building on the brand wash. -->
							<Avatar.Root class="bg-brand-subtle size-10 shrink-0">
								{#if adviser.photoUrl}
									<Avatar.Image src={adviser.photoUrl} alt="" class="object-contain p-1.5" />
								{/if}
								<Avatar.Fallback class="bg-brand-subtle text-brand-strong">
									<Building2Icon class="size-4" aria-hidden="true" />
								</Avatar.Fallback>
							</Avatar.Root>
						{:else if adviser.photoUrl}
							<Avatar.Root class="size-10 shrink-0">
								<Avatar.Image src={adviser.photoUrl} alt="" class="object-cover" />
								<Avatar.Fallback>
									<PersonAvatar name={adviser.name} size="md" class="size-10" />
								</Avatar.Fallback>
							</Avatar.Root>
						{:else}
							<PersonAvatar name={adviser.name} size="md" class="size-10" />
						{/if}
						<span class="min-w-0 flex-1">
							<span class="block truncate text-sm font-semibold">{adviser.name}</span>
							<span class="text-muted-foreground block truncate text-xs">
								{#if isFirm}
									Your firm · Client support
								{:else}
									{adviser.jobTitle ?? 'Your adviser'} · {adviser.firm}
								{/if}
							</span>
						</span>
					</div>
				{:else}
					<div class="flex min-w-0 flex-1 items-center gap-3 px-2 py-1.5">
						<span
							class="bg-primary text-primary-foreground flex size-10 shrink-0 items-center justify-center rounded-full"
							aria-hidden="true"
						>
							<UserRoundIcon class="size-5" />
						</span>
						<span class="block text-sm font-semibold">Your firm</span>
					</div>
				{/if}
			</div>

			{#if isEmpty}
				<!-- `justify-end`: the invitation sits directly above the composer rather than
				     floating mid-card. A conversation grows upwards from the box you type into, so
				     the prompt belongs where the first message will appear. -->
				<div
					class="flex flex-1 flex-col items-center justify-end gap-2 px-6 pt-10 pb-6 text-center"
				>
					<h1 class="text-lg font-semibold tracking-tight">
						Message {firstName ?? 'your firm'}
					</h1>
					<p class="text-muted-foreground max-w-sm text-sm">
						Ask a question, send a document, or let {isFirm ? 'us' : 'them'} know something has changed.
						Only you and your firm can see this conversation.
					</p>
				</div>
			{:else}
				<MessageThread
					{messages}
					viewer="client"
					clientName={() => myName}
					{firstUnreadId}
					download={(_, attachment) => `/messages/attachments/${attachment}`}
					onFulfil={openFulfil}
					bind:scrollEl={threadEl}
				/>
			{/if}

			<!-- ONE composer, docked at the foot of the card in both states. The card runs to the
			     bottom edge on a phone, so the safe area is paid here, under the box. -->
			<div class="shrink-0 px-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:px-4 sm:pb-4">
				<MessageComposer
					bind:draft
					bind:files={draftFiles}
					error={sendError}
					{sending}
					submit={send}
					enterSends
					compact
					placeholder={firstName ? `Message ${firstName}…` : 'Write a message…'}
				/>
			</div>
		</section>
	</div>
{:else}
	<div class="mx-auto max-w-lg space-y-6">
		{#if data.loadError}
			<LoadErrorBanner
				message="We couldn’t load your messages. Please try again."
				onRetry={() => invalidateAll()}
			/>
		{:else}
			<!-- A failure, not an absence: the sibling branch above it is a LoadErrorBanner, and this
			     one wore the neutral dashed frame while telling the reader to "try again in a
			     moment" beside nothing to try again with. -->
			<EmptyState
				tone="warning"
				icon={UserRoundIcon}
				title="We couldn’t open your messages"
				description="Please try again."
			>
				{#snippet action()}
					<Button variant="outline" onclick={() => invalidateAll()}>Try again</Button>
				{/snippet}
			</EmptyState>
		{/if}
	</div>
{/if}

<!-- Fulfil a document request straight from its chat card. -->
<Dialog.Root
	bind:open={fulfillOpen}
	onOpenChange={(o) => {
		if (!o) {
			fulfillFiles = [];
			fulfillTarget = null;
			fulfillError = '';
		}
	}}
>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>{fulfillTarget?.title ?? 'Upload a document'}</Dialog.Title>
			<Dialog.Description>
				Send this to your adviser. PDF, Word, Excel, PowerPoint or text, up to 25 MB.
			</Dialog.Description>
		</Dialog.Header>
		<div class="space-y-2 py-2">
			<FileDropzone
				bind:files={fulfillFiles}
				accept={ATTACHMENT_ACCEPT}
				rich
				title="Upload document"
				formats="PDF, Word, Excel, PowerPoint, TXT"
				onreject={(reason) => (fulfillError = reason)}
			/>
			{#if fulfillError}
				<p class="text-destructive text-xs" role="alert">{fulfillError}</p>
			{/if}
		</div>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (fulfillOpen = false)}>Cancel</Button>
			<Button disabled={fulfillFiles.length === 0 || fulfilling} onclick={submitFulfilment}>
				{fulfilling ? 'Sending…' : 'Send'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
