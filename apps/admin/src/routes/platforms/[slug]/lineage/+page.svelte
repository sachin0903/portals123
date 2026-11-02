<script lang="ts">
	// Lineage gets the whole page now that it isn't sharing space with a tab strip. The graph is
	// the point of this section, so the canvas takes the viewport height less the shell chrome
	// (header, connection line, the graph's own toolbar) with a floor so it never collapses on a
	// short window.
	import LineageTab from '$lib/platforms/components/LineageTab.svelte';
	import { usePlatformStore } from '$lib/platforms/context';

	const holder = usePlatformStore();
	const store = $derived(holder.store);
</script>

<LineageTab {store} heightClass="h-[max(560px,calc(100vh-20rem))]" />
