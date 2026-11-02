<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { deserialize } from '$app/forms';
	import { Button, EmptyState, LoadErrorBanner, RecordSkeleton, Streamed } from '@graphene/ui';
	import FileXIcon from '@lucide/svelte/icons/file-x';
	import GenerateDialog from '$lib/components/documents/generate-dialog.svelte';
	import type { RenderStatus } from '$lib/components/documents/render-progress';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Enqueue via the server action, then poll the document's status until the worker finishes
	// (docs/document-generation.md §8.1). On `generated` navigate to it; on `failed`/timeout return an
	// error message (the dialog surfaces it).
	//
	// EVERY STATUS IS REPORTED BACK as it is read, so the dialog draws the server's own phase. The
	// wait is up to two minutes and `queued` (nobody has claimed it) fails for a different reason
	// from `generating` (a worker is rendering it) — a single spinner over both made a render worker
	// that was not running indistinguishable from a slow template.
	async function handleGenerate(
		templateUid: string | undefined,
		input: {
			name: string;
			clientContactId: number | null;
			answers: Record<string, unknown>;
		},
		onProgress?: (status: RenderStatus) => void,
		onCancellable?: (cancel: () => Promise<string>) => void,
		onEmpty?: (note: string) => void
	): Promise<string | null> {
		const body = new FormData();
		body.append('payload', JSON.stringify({ templateUid, ...input }));
		const res = await fetch('?/generate', { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type !== 'success' || !result.data?.uid) {
			if (result.type === 'failure') return (result.data?.error as string) ?? 'Generation failed.';
			return 'Generation failed.';
		}
		const uid = result.data.uid as string;
		// Enqueued: it exists and is waiting, which is already more than "submitting".
		onProgress?.('queued');

		// The document exists now, so there is something to cancel. `cancelled` ends the poll below
		// on its next tick — the loop checks it rather than being aborted, so a cancel that races a
		// finished render still lets the completed document win.
		let cancelled = false;
		onCancellable?.(async () => {
			cancelled = true;
			const res = await fetch(`/documents/${uid}/cancel-render`, { method: 'POST' });
			if (!res.ok) {
				const body = await res.json().catch(() => ({}));
				return (body as { message?: string }).message ?? 'Could not cancel the render.';
			}
			const { detail } = (await res.json()) as { detail: string };
			return detail;
		});

		// Poll ~2s for up to ~2 minutes while the worker renders. A persistent non-OK probe is a real
		// failure (403/404), not "still rendering" — give up rather than silently timing out.
		let probeFailures = 0;
		for (let i = 0; i < 60; i++) {
			await new Promise((r) => setTimeout(r, 2000));
			// Cancelled while we slept: stop polling and say nothing. The cancel call already
			// reported its own outcome, and a second message over the top of it would be noise.
			if (cancelled) return null;
			const s = await fetch(`/documents/${uid}/status`);
			if (!s.ok) {
				probeFailures += 1;
				if (probeFailures >= 3) {
					return 'The document is being prepared, but its progress is unavailable. Check the documents list.';
				}
				continue;
			}
			probeFailures = 0;
			const { status, note } = (await s.json()) as { status: string; note: string | null };
			// Only the phases the progress UI models; anything else leaves it where it was rather
			// than resetting the steps to a phase the document is not in.
			if (status === 'queued' || status === 'generating' || status === 'generated') {
				onProgress?.(status);
			}
			if (status === 'generated') {
				// A document can generate AND still be worth a word: the worker records "Dataset X
				// returned no rows" for every table that came out empty. That note is the difference
				// between "the binding is broken" and "this client has none of those", and without it
				// an empty table is a mystery the author has no way to resolve.
				if (note) onEmpty?.(note);
				await goto(`/documents/${uid}`);
				return null;
			}
			if (status === 'failed') {
				onProgress?.('failed');
				// The worker's own sentence when it left one: it names the cause, where the generic
				// line sent the author to re-read a template that may be perfectly fine.
				return note ? `Rendering failed: ${note}` : 'Rendering failed. Check the template.';
			}
		}
		// Still queued after two minutes. That normally means the render worker isn't running — say so
		// rather than dropping the user on the list with no explanation.
		return 'This document is taking longer than expected. Check the documents list shortly, or ask your administrator for help.';
	}

	/**
	 * Queue one document per client and hand the dialog the report.
	 *
	 * No polling, deliberately — see the server action. The idempotency key is minted ONCE per page
	 * visit rather than per click, which is what makes a double-click (or a retry after a dropped
	 * response) return the same documents instead of a second set for every client.
	 */
	const batchKey = crypto.randomUUID();
	async function handleGenerateBatch(
		templateUid: string | undefined,
		input: {
			name: string;
			contactIds: number[];
			answers: Record<string, unknown>;
		}
	): Promise<
		{ queued: number; rejected: { contactId: number; reason: string }[] } | { error: string }
	> {
		const body = new FormData();
		body.append('payload', JSON.stringify({ templateUid, idempotencyKey: batchKey, ...input }));
		const res = await fetch('?/generateBatch', { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type === 'failure') {
			return { error: (result.data?.error as string) ?? 'Generation failed.' };
		}
		if (result.type !== 'success' || typeof result.data?.queued !== 'number') {
			return { error: 'Generation failed.' };
		}
		return {
			queued: result.data.queued as number,
			rejected: (result.data.rejected ?? []) as { contactId: number; reason: string }[]
		};
	}
</script>

<svelte:head>
	<title>Generate document · Graphene Platform</title>
</svelte:head>

<Streamed data={data.wizard} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={2} />
	{/snippet}
	{#snippet children(wizard)}
		{#if wizard.loadError}
			<!-- The questionnaire, the client list or the template itself failed to load. Opening the dialog
			     anyway would let someone generate a document from half its inputs, so don't. -->
			<div class="mx-auto mt-16 max-w-lg space-y-4">
				<LoadErrorBanner
					message="This template’s details couldn’t be loaded. Try again."
					onRetry={() => invalidateAll()}
				/>
				<Button variant="ghost" href="/documents">Back to documents</Button>
			</div>
		{:else if !wizard.template}
			<EmptyState icon={FileXIcon} title="No template selected" class="mx-auto mt-16 max-w-md">
				{#snippet action()}
					<Button href="/templates" variant="ghost">Browse templates</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<GenerateDialog
				template={wizard.template}
				fields={wizard.fields}
				clients={wizard.clients}
				templateVars={wizard.templateVars}
				onClose={() => goto('/documents')}
				onGenerate={(input, onProgress, onCancellable, onEmpty) =>
					handleGenerate(wizard.template?.uid, input, onProgress, onCancellable, onEmpty)}
				onGenerateBatch={(input) => handleGenerateBatch(wizard.template?.uid, input)}
			/>
		{/if}
	{/snippet}
</Streamed>
