<script lang="ts">
	// A match's state as a chip — the shared StatusBadge, so it wears the same geometry as the
	// contribution status chip it sits beside. It was `Badge variant="outline"` carrying a pasted
	// copy of the shared tone classes, which gave two chips on one row two different radii.
	import { StatusBadge } from '@graphene/ui';
	import { MATCH_LABEL, MATCH_TONE } from '$lib/contributions/status';
	import type { MatchStatus } from '$lib/contributions/types';

	let { status }: { status: MatchStatus } = $props();
</script>

<StatusBadge tone={MATCH_TONE[status]}>{MATCH_LABEL[status]}</StatusBadge>
