<script lang="ts">
	// Graphene's internal notes on the client: why something was allowed, a call made, enhanced due
	// diligence done. Graphene only: the firm and the client never see them (tipping off). A note is
	// never edited or deleted, so the list is the record, newest first.
	import { enhance } from '$app/forms';
	import { Button, Card, CardContent, CardHeader, CardTitle, Label, Textarea } from '@graphene/ui';
	import NotebookPenIcon from '@lucide/svelte/icons/notebook-pen';
	import { dateTime, type AmlClientNote } from '$lib/aml';
	import { submit } from '$lib/compliance/submit';

	let { notes, canWrite }: { notes: AmlClientNote[]; canWrite: boolean } = $props();

	let body = $state('');
	let saving = $state(false);
</script>

<Card glass>
	<CardHeader class="border-border space-y-1 border-b">
		<CardTitle class="flex items-center gap-2 text-base">
			<NotebookPenIcon class="text-muted-foreground size-4" aria-hidden="true" />
			Internal notes
		</CardTitle>
		<p class="text-muted-foreground text-xs">
			Graphene only. Never shown to the firm or the client.
		</p>
	</CardHeader>

	<CardContent class="space-y-4 text-sm">
		{#if canWrite}
			<form
				method="POST"
				action="?/addNote"
				use:enhance={submit(
					'Note added.',
					(pending) => (saving = pending),
					() => (body = '')
				)}
				class="space-y-2"
			>
				<Label for="client-note" class="sr-only">New note</Label>
				<Textarea
					id="client-note"
					name="body"
					bind:value={body}
					rows={3}
					maxlength={5000}
					placeholder="Why something was allowed, a call made, due diligence done."
				/>
				<div class="flex justify-end">
					<Button type="submit" size="sm" disabled={saving || !body.trim()}>
						{saving ? 'Adding…' : 'Add note'}
					</Button>
				</div>
			</form>
		{/if}

		{#if notes.length}
			<ul class="divide-border divide-y">
				{#each notes as note (note.uid)}
					<li class="space-y-1 py-3 first:pt-0 last:pb-0">
						<p class="text-muted-foreground text-xs">
							{note.author} · <time datetime={note.created_at}>{dateTime(note.created_at)}</time>
						</p>
						<p class="break-words whitespace-pre-wrap">{note.body}</p>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="text-muted-foreground">No notes yet.</p>
		{/if}
	</CardContent>
</Card>
