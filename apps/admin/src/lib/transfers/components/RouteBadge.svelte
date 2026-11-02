<script lang="ts">
	import { Badge } from '@graphene/ui';
	import { ROUTE_LABEL, ROUTE_TONE } from '$lib/transfers/status';
	import type { TransferRoute } from '$lib/transfers/types';

	let { route }: { route: TransferRoute } = $props();
</script>

<Badge variant="outline" class={ROUTE_TONE[route]}>{ROUTE_LABEL[route]}</Badge>
