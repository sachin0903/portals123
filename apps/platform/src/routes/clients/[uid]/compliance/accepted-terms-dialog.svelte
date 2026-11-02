<script lang="ts">
	// The terms a client accepted, exactly as they read them: the version the acceptance names, not
	// today's. Its text, or its file in the document viewer; an in-person acceptance links its signed
	// copy too.
	import { onMount } from 'svelte';
	import { Dialog, DocumentViewer, formatDate, LoadErrorBanner } from '@graphene/ui';
	import type { AcceptedTerms, TermAcceptance } from './+page.server';

	let {
		contactUid,
		acceptance,
		onclose
	}: { contactUid: string; acceptance: TermAcceptance; onclose: () => void } = $props();

	const base = $derived(
		`/clients/${encodeURIComponent(contactUid)}/compliance/terms/${encodeURIComponent(acceptance.uid)}`
	);
	let terms = $state<AcceptedTerms | null>(null);
	let failed = $state(false);

	async function load(): Promise<void> {
		failed = false;
		try {
			const res = await fetch(base);
			if (!res.ok) throw new Error(String(res.status));
			terms = (await res.json()) as AcceptedTerms;
		} catch {
			failed = true;
		}
	}

	onMount(() => void load());
</script>

<Dialog.Root open onOpenChange={(open) => !open && onclose()}>
	<Dialog.Content class="flex max-h-[90svh] flex-col sm:max-w-3xl">
		<Dialog.Header>
			<Dialog.Title>{acceptance.title} · v{acceptance.version_number}</Dialog.Title>
			<Dialog.Description>
				Accepted {formatDate(acceptance.accepted_at)} · {acceptance.channel === 'in_person'
					? 'In person'
					: 'Portal'}
				{#if acceptance.evidence_document_uid}
					·
					<a
						href={`/documents/${encodeURIComponent(acceptance.evidence_document_uid)}`}
						class="text-foreground font-medium underline-offset-4 hover:underline"
					>
						Signed copy
					</a>
				{/if}
			</Dialog.Description>
		</Dialog.Header>

		<div class="min-h-0 flex-1 overflow-y-auto">
			{#if failed}
				<LoadErrorBanner message="The terms could not be loaded." onRetry={() => load()} />
			{:else if !terms}
				<p class="text-muted-foreground text-sm">Loading the terms…</p>
			{:else if terms.has_file}
				<DocumentViewer src={`${base}/file`} contentType="application/pdf" name={terms.code} />
			{:else if terms.body_html}
				<div class="prose prose-sm max-w-none rounded-lg border p-4">
					<!-- Terms text, sanitised when the version was published. -->
					<!-- eslint-disable-next-line svelte/no-at-html-tags -->
					{@html terms.body_html}
				</div>
			{:else}
				<p class="text-muted-foreground text-sm">This version has no text to show.</p>
			{/if}
		</div>
	</Dialog.Content>
</Dialog.Root>
