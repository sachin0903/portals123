<script lang="ts">
	import { LoadErrorBanner, Streamed } from '@graphene/ui';
	import {
		MessagesInbox,
		MessagesInboxSkeleton,
		type MessageRequestTarget,
		type MessagesRoutes
	} from '@graphene/ui/book/messages';
	import { RequestDialog } from '@graphene/ui/book/documents';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { untrack } from 'svelte';
	import { readingContact } from '$lib/reading.svelte';

	let { data } = $props();
	let requestTarget = $state<MessageRequestTarget | null>(null);
	let requestOpen = $derived(requestTarget !== null);
	// `notes` is WITHHELD from a reader without `contacts:write`: no route, no composer, so the
	// rail never offers a box whose submit can only come back 403. `untrack` because a permission
	// is read once at construction, like every other route on this object — rebuilding them all
	// when the page's data changes would tear down the inbox's event stream with them.
	const routes: MessagesRoutes = {
		conversations: '/chats',
		search: (query) => `/chats/search${query ? `?q=${encodeURIComponent(query)}` : ''}`,
		contacts: (query) => `/chats/contacts${query ? `?q=${encodeURIComponent(query)}` : ''}`,
		groups: '/chats/groups',
		conversation: (uid) => `/chats/${uid}`,
		group: (uid) => `/chats/groups/${uid}`,
		conversationAttachments: (uid) => `/chats/${uid}/attachments`,
		groupAttachments: (uid) => `/chats/groups/${uid}/attachments`,
		read: (uid) => `/chats/${uid}/read`,
		download: (conversationUid, attachmentUid) =>
			`/chats/${conversationUid}/attachments/${attachmentUid}`,
		events: '/messages/events',
		rail: (contactUid, contactId) => `/chats/rail/${contactUid}?contact_id=${contactId}`,
		notes: untrack(() => data.canWriteNotes)
			? (contactUid: string) => `/chats/notes/${contactUid}`
			: undefined,
		note: untrack(() => data.canWriteNotes)
			? (contactUid: string, noteUid: string) => `/chats/notes/${contactUid}/${noteUid}`
			: undefined
	};
</script>

<svelte:head><title>Messages · Platform</title></svelte:head>

<Streamed data={data.inbox} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<MessagesInboxSkeleton />
	{/snippet}
	{#snippet children(inbox)}
		{#if inbox.loadError}
			<LoadErrorBanner
				message="We couldn’t load the firm’s messages. Try again."
				onRetry={() => invalidateAll()}
			/>
		{:else}
			<MessagesInbox
				conversations={inbox.conversations}
				groups={inbox.groups}
				openConversation={data.openConversation}
				openGroup={data.openGroup}
				canRequestDocuments={data.canRequestDocuments}
				canWrite={data.canWriteMessages}
				showTasks={false}
				{routes}
				links={{
					request: (uid) => `/documents/requests/${uid}`,
					task: (uid) => `/tasks/${uid}`,
					client: (uid) => `/clients/${uid}`,
					account: (uid) => `/accounts/${uid}`
				}}
				onRequest={(target) => (requestTarget = target)}
				onReadingContact={readingContact}
			/>
		{/if}
	{/snippet}
</Streamed>

{#if requestTarget?.kind === 'conversation'}
	<RequestDialog
		{enhance}
		invalidate={invalidateAll}
		open={requestOpen}
		onCreated={() => {
			requestTarget = null;
		}}
		action="?/createRequest"
		forClient={{ contact_id: requestTarget.contact_id, name: requestTarget.name, email: null }}
	/>
{:else if requestTarget?.kind === 'group'}
	<RequestDialog
		{enhance}
		invalidate={invalidateAll}
		open={requestOpen}
		onCreated={async () => {
			if (requestTarget?.kind === 'group') await requestTarget.refresh();
			requestTarget = null;
		}}
		action="?/createGroupRequest"
		forGroup={{ id: requestTarget.id, uid: requestTarget.uid, name: requestTarget.name }}
	/>
{/if}
