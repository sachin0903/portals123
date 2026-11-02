<script lang="ts">
	import QueryTab from '$lib/platforms/components/QueryTab.svelte';
	import { usePlatformStore } from '$lib/platforms/context';

	const holder = usePlatformStore();
	const store = $derived(holder.store);
</script>

<QueryTab {store} />
