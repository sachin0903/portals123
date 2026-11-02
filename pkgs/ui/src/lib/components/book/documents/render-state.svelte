<script lang="ts">
	// A queued or failed render: the badge, the reason, and a retry when `onretry` is passed.
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import { Button } from '../../ui/button/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { STATUS_META } from './display.js';
	import type { LibraryRow } from './documents.js';

	let {
		doc,
		retrying = false,
		onretry
	}: { doc: LibraryRow; retrying?: boolean; onretry?: () => void } = $props();
	const meta = $derived(STATUS_META[doc.status]);
</script>

<StatusBadge tone={meta.tone} pulse={meta.pulse}>{meta.label}</StatusBadge>
{#if doc.status === 'failed' && doc.error}
	<span class="text-destructive truncate text-xs" title={doc.error}>{doc.error}</span>
{/if}
{#if doc.status === 'failed' && onretry}
	<Button
		variant="ghost"
		size="icon"
		class="text-muted-foreground hover:text-foreground size-7 shrink-0"
		aria-label={`Retry rendering ${doc.name}`}
		title="Retry rendering"
		disabled={retrying}
		onclick={onretry}
	>
		<RotateCcwIcon class="size-3.5" />
	</Button>
{/if}
