<script lang="ts">
	import { LoadErrorBanner } from '@graphene/ui';
	import {
		MessagesInbox,
		type MessageRequestTarget,
		type MessagesRoutes
	} from '@graphene/ui/book/messages';
	import { RequestDialog } from '@graphene/ui/book/documents';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';

	let { data } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/operate/messages/api`);
	let requestTarget = $state<MessageRequestTarget | null>(null);
	const routes = $derived<MessagesRoutes>({
		conversations: `${base}/conversations`,
		search: (query) => `${base}/conversations${query ? `?q=${encodeURIComponent(query)}` : ''}`,
		contacts: (query) => `${base}/contacts${query ? `?q=${encodeURIComponent(query)}` : ''}`,
		groups: `${base}/groups`,
		conversation: (uid) => `${base}/conversations/${uid}/messages`,
		group: (uid) => `${base}/groups/${uid}/messages`,
		conversationAttachments: (uid) => `${base}/conversations/${uid}/attachments`,
		groupAttachments: (uid) => `${base}/groups/${uid}/attachments`,
		read: (uid) => `${base}/conversations/${uid}/read`,
		download: (conversationUid, attachmentUid) =>
			`${base}/conversations/${conversationUid}/attachments/${attachmentUid}`,
		events: `/tenants/${data.tenant.uid}/operate/messages/events`
	});
</script>

<svelte:head><title>Messages · {data.tenant.name}</title></svelte:head>

{#if data.loadError}
	<LoadErrorBanner
		message="We couldn't load this firm's message threads just now."
		onRetry={() => invalidateAll()}
	/>
{:else}
	<MessagesInbox
		conversations={data.conversations}
		groups={data.groups}
		canWrite={data.writing}
		canRequestDocuments={data.writing}
		{routes}
		onRequest={(target) => (requestTarget = target)}
	/>
{/if}

{#if requestTarget?.kind === 'conversation'}
	<RequestDialog
		{enhance}
		invalidate={invalidateAll}
		open
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
		open
		onCreated={async () => {
			if (requestTarget?.kind === 'group') await requestTarget.refresh();
			requestTarget = null;
		}}
		action="?/createGroupRequest"
		forGroup={{ id: requestTarget.id, uid: requestTarget.uid, name: requestTarget.name }}
	/>
{/if}
