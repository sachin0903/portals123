<script lang="ts">
	// The client's message thread — the record's "Messages" section.
	import { EmptyState, LoadErrorBanner, RecordSkeleton, Streamed } from '@graphene/ui';
	import MessageSquareIcon from '@lucide/svelte/icons/message-square';
	import { invalidateAll } from '$app/navigation';
	import ClientChat from '$lib/components/clients/client-chat.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	/** THE SAME PROXY THE INBOX POSTS TO, so a note written beside a thread is one kind of note
	 *  wherever it was typed. `invalidateAll` afterwards rather than patching the panel: this page
	 *  also renders the record's own Notes card on the Overview tab beside it, and the two must not
	 *  disagree about what the client's notes are. */
	async function addNote(body: string): Promise<string | null> {
		const res = await fetch(`/chats/notes/${data.detail.contact.uid}`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ body })
		});
		if (!res.ok) {
			return ((await res.json().catch(() => null))?.error as string) ?? "Couldn't save that note.";
		}
		await invalidateAll();
		return null;
	}

	/** The mirror of `addNote`, through the same proxy the inbox deletes with; `invalidateAll` for
	 *  the same reason — the Overview's Notes card must not still list a note this tab removed. */
	async function deleteNote(uid: string): Promise<string | null> {
		const res = await fetch(`/chats/notes/${data.detail.contact.uid}/${uid}`, { method: 'DELETE' });
		if (!res.ok) {
			return (
				((await res.json().catch(() => null))?.error as string) ?? "Couldn't delete that note."
			);
		}
		await invalidateAll();
		return null;
	}
</script>

<svelte:head><title>Messages · {data.clientName}</title></svelte:head>

<Streamed data={data.thread} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton fill header={false} />
	{/snippet}
	{#snippet children(thread)}
		<!-- Cancels the shell's own content padding so the chat pane can reach the true bottom of the
		     viewport — same technique the Activity and Event logs sections use. No `bg-card`: those
		     sections sit on `--background`, and a different fill here (especially in dark mode) reads as
		     a different application. -->
		<div
			class="-m-4 flex h-[calc(100svh-var(--topbar-h,4rem))] flex-col gap-5 overflow-hidden p-4 md:-m-6 md:p-6"
		>
			{#if thread.loadError}
				<LoadErrorBanner
					message="This client's messages couldn't be loaded."
					onRetry={() => invalidateAll()}
				/>
			{:else if !thread.conversation}
				<EmptyState
					icon={MessageSquareIcon}
					title="No messages yet"
					description="Nothing has been sent on this client's thread."
				/>
			{:else}
				<div class="min-h-0 w-full flex-1">
					<ClientChat
						conversation={thread.conversation}
						initialMessages={thread.messages}
						canRequestDocuments={data.canRequestDocuments}
						canWriteMessages={data.canWriteMessages}
						advisers={data.detail.advisers}
						client={thread.client}
						accounts={thread.accounts}
						notes={thread.notes}
						onAddNote={data.canWriteNotes ? addNote : undefined}
						onDeleteNote={data.canWriteNotes ? deleteNote : undefined}
						requests={thread.requests}
						tasks={thread.tasks}
						links={{
							request: (uid) => `/documents/requests/${uid}`,
							task: (uid) => `/tasks/${uid}`,
							account: (uid) => `/accounts/${uid}`
						}}
					/>
				</div>
			{/if}
		</div>
	{/snippet}
</Streamed>
