<script lang="ts">
	// Every card is now the API's answer. The only figure shown is `enabled_firms`, which is a
	// cheap true fact from the portals database; entity counts live one click away on the
	// overview, which already computes them from the warehouse. Showing two invented KPIs per
	// card next to a live status badge was strictly worse than showing none.
	import { Button, PageHeader } from '@graphene/ui';
	import ScrollTextIcon from '@lucide/svelte/icons/scroll-text';
	import PlatformCard from '$lib/platforms/components/PlatformCard.svelte';

	let { data } = $props();
</script>

<PageHeader
	title="Platforms"
	description="Custodian and scheme-administrator data, imports and service health."
>
	{#snippet actions()}
		<!-- Cross-provider, so it belongs beside the index rather than inside one platform. -->
		<Button variant="outline" size="sm" href="/platforms/api">
			<ScrollTextIcon class="mr-1.5 size-4" /> API log
		</Button>
	{/snippet}
</PageHeader>

<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
	{#each data.statuses as status (status.provider)}
		<PlatformCard {status} />
	{/each}
</div>
