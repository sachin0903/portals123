<script lang="ts">
	import InfoIcon from '@lucide/svelte/icons/info';

	// The 'banner' message look — a full-width notice band for closures and deadlines: brand
	// wash (--brand-subtle; the brief's "--brand-soft" — theme.css's toning system names it
	// subtle), hairline brand-line border, an info glyph, title semibold with the body
	// beneath. Same contract as message-card: plain text ONLY (no raw-html blocks ever —
	// Svelte's escaping is the boundary for firm-supplied copy), whitespace-pre-line for line
	// breaks, and nothing at all when both fields are empty.
	let { title = '', body = '' }: { title?: string; body?: string } = $props();

	const hasTitle = $derived(title.trim() !== '');
	const hasBody = $derived(body.trim() !== '');
</script>

{#if hasTitle || hasBody}
	<div class="border-brand-line bg-brand-subtle flex items-start gap-3 rounded-lg border px-4 py-3">
		<InfoIcon class="text-brand-strong mt-0.5 size-4 shrink-0" aria-hidden="true" />
		<div class="min-w-0 text-sm">
			{#if hasTitle}
				<p class="font-semibold">{title}</p>
			{/if}
			{#if hasBody}
				<p class={['whitespace-pre-line', hasTitle && 'mt-0.5']}>{body}</p>
			{/if}
		</div>
	</div>
{/if}
