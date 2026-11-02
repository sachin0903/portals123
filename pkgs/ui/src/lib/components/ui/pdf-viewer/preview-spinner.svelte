<script lang="ts">
	// The stylised "rendering" pill shared across the template preview surfaces (the overview card
	// and the pdf.js viewer): a brand spinner in a soft card pill, floated over the preview area so
	// it reads the same whether a preview is being fetched or drawn. Absolutely positioned — the
	// parent must be `relative`. Mirrors the in-editor re-render overlay so the whole feature matches.
	let { label = 'Rendering preview…' }: { label?: string } = $props();
</script>

<div class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
	<div
		class="bg-card ring-foreground/10 flex items-center gap-2 rounded-full px-3.5 py-2 text-xs font-medium shadow-sm ring-1"
	>
		<svg class="text-brand size-4 animate-spin" viewBox="0 0 24 24" fill="none">
			<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
			<path
				class="opacity-75"
				fill="currentColor"
				d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4z"
			/>
		</svg>
		{label}
	</div>
</div>
