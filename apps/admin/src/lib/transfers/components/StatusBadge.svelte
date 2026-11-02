<script lang="ts">
	// A transfer's status as a chip.
	//
	// A LABEL ADAPTER over `@graphene/ui`'s StatusBadge, not a chip of its own: this workbench
	// speaks a status in two vocabularies (the ops one and the adviser-facing one), and choosing
	// between them is the only thing local about it. It used to be `Badge variant="outline"` with a
	// pasted copy of the shared tone classes, so it wore Badge's geometry while every other status
	// chip in the portal wore StatusBadge's — a different radius and padding for the same idea.
	import { StatusBadge } from '@graphene/ui';
	import { ADVISER_LABEL, STATUS_LABEL, statusTone } from '$lib/transfers/status';
	import type { TransferStatus } from '$lib/transfers/types';

	let { status, adviser = false }: { status: TransferStatus; adviser?: boolean } = $props();
</script>

<StatusBadge tone={statusTone(status)}>
	{adviser ? ADVISER_LABEL[status] : STATUS_LABEL[status]}
</StatusBadge>
