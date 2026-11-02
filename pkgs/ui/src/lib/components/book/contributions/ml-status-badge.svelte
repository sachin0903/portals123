<script lang="ts">
	// Morgan Lloyd's own `cam_status`, shown beside Graphene's pipeline status.
	//
	// Two badges rather than one, for the same reason the expectation badge exists: they answer
	// different questions and can legitimately disagree. Graphene's status is the whole journey;
	// ML's is the state of the back-office record — and it is ML's, not ours, that decides
	// whether the contribution can still be amended.
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { ML_STATUS_LABELS } from './types.js';
	import { ML_STATUS_TONE } from './status.js';

	let {
		status,
		observedAt = null
	}: { status: number | null | undefined; observedAt?: string | null } = $props();

	const label = $derived(status == null ? null : (ML_STATUS_LABELS[status] ?? `Status ${status}`));
	const tone = $derived(status == null ? 'neutral' : (ML_STATUS_TONE[status] ?? 'neutral'));
</script>

{#if label}
	<span title={observedAt ? `Morgan Lloyd status, last read ${observedAt}` : 'Morgan Lloyd status'}>
		<StatusBadge {tone}>ML: {label}</StatusBadge>
	</span>
{/if}
