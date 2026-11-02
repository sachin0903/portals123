<script lang="ts">
	// A contribution's lifecycle stage as a chip.
	//
	// A LABEL ADAPTER over `@graphene/ui`'s StatusBadge, not a chip of its own: this workbench
	// speaks a stage in two vocabularies (the ops one and the adviser-facing one), and choosing
	// between them is the only thing local about it. It used to be `Badge variant="outline"` with a
	// pasted copy of the shared tone classes, so it wore Badge's geometry while every other status
	// chip in the portal wore StatusBadge's — a different radius and padding for the same idea.
	import { StatusBadge } from '@graphene/ui';
	import { ADVISER_LABEL, STATUS_LABEL, STATUS_TONE } from '$lib/contributions/status';
	import type { ContributionStatus } from '$lib/contributions/types';

	let { status, adviser = false }: { status: ContributionStatus; adviser?: boolean } = $props();
</script>

<StatusBadge tone={STATUS_TONE[status]}>
	{adviser ? ADVISER_LABEL[status] : STATUS_LABEL[status]}
</StatusBadge>
