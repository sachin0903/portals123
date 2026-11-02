<script lang="ts">
	import * as Card from '../ui/card/index.js';

	// A firm-authored welcome/notice card. Plain text ONLY — Svelte's default escaping is the
	// security boundary (firm-supplied copy must never become markup), so no {@html} ever; line
	// breaks survive via whitespace-pre-line. Renders nothing when there is nothing to say.
	let { title = '', body = '' }: { title?: string; body?: string } = $props();

	const hasTitle = $derived(title.trim() !== '');
	const hasBody = $derived(body.trim() !== '');
</script>

{#if hasTitle || hasBody}
	<Card.Root glass class="h-full">
		{#if hasTitle}
			<Card.Header>
				<Card.Title>{title}</Card.Title>
			</Card.Header>
		{/if}
		{#if hasBody}
			<Card.Content>
				<p class="text-sm whitespace-pre-line">{body}</p>
			</Card.Content>
		{/if}
	</Card.Root>
{/if}
