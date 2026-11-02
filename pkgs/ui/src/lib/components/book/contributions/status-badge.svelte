<script lang="ts">
	// The badge says the STAGE, not the raw status — one vocabulary across the metrics, the status
	// filter and the queue. The TONE stays per-status, so `manual_intervention` reads danger inside
	// the Not lodged stage.
	import { STATUS_LABEL, STATUS_TONE, stageOf } from './status';
	import type { ContributionStatus } from './types';
	import { StatusBadge } from '../../ui/status-badge/index.js';

	let { status }: { status: ContributionStatus } = $props();

	// `STATUS_LABEL` is the fallback: a status with no stage would otherwise render an empty badge.
	const text = $derived(stageOf(status)?.label ?? STATUS_LABEL[status]);
</script>

<StatusBadge tone={STATUS_TONE[status]}>{text}</StatusBadge>
