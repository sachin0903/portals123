<script lang="ts">
	// The 'note' message look — the letter. No card at all: a 3px brand rule down the left
	// edge, the title set in the display face, the body at a comfortable reading measure.
	// Plain text ONLY — Svelte's default escaping is the security boundary (firm-supplied copy
	// must never become markup), so no raw-html blocks ever, same as message-card; line breaks
	// survive via whitespace-pre-line. Renders nothing when there is nothing to say.
	let { title = '', body = '' }: { title?: string; body?: string } = $props();

	const hasTitle = $derived(title.trim() !== '');
	const hasBody = $derived(body.trim() !== '');
</script>

{#if hasTitle || hasBody}
	<!-- border-s (logical start), so the rule stays on the reading edge in RTL. -->
	<div class="border-brand border-s-[3px] py-1 ps-4">
		{#if hasTitle}
			<!-- The display face — themable per tenant; falls back to the body face, never the
			     UA serif (the ledger header's fallback-chain rule). -->
			<p
				class="text-lg font-medium tracking-tight"
				style="font-family: var(--font-display, var(--font-sans))"
			>
				{title}
			</p>
		{/if}
		{#if hasBody}
			<p class={['max-w-prose text-sm leading-relaxed whitespace-pre-line', hasTitle && 'mt-2']}>
				{body}
			</p>
		{/if}
	</div>
{/if}
